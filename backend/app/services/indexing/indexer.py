"""Incremental search indexing of a project's analyzed chunks.

Runs synchronously in the calling request thread (one run per project at a time, guarded
in-process). The steps are ordered so a crash or error at any point leaves a consistent index:

1. Reconcile: compare the project's ChromaDB ids with the SQLite manifest. Manifest entries whose
   vector is missing are re-embedded; vectors without a manifest entry (left by a failed run)
   are reused if still wanted (ids are content-addressed) and deleted otherwise.
2. Diff the current chunks against the manifest: unchanged chunks are kept, new or changed ones
   are embedded (or reuse the vector of an identical embedding input, e.g. a moved chunk),
   removed ones are dropped.
3. Write new vectors to ChromaDB (idempotent upserts). Progress is committed per batch.
4. One SQLite transaction swaps the manifest and FTS rows and marks the index ready.
5. Only after that commit are obsolete vectors (and collections of a previous model) deleted.

SQLite and ChromaDB do not share a transaction: a failure before step 4 keeps the previous
manifest (and its vectors, which are only deleted in step 5) intact and marks the index failed;
a failure in step 5 leaves unreferenced vectors that search ignores and the next run removes.
"""
import hashlib
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import CodeChunk, CodeSymbol, IndexEntry, Project, SearchIndex
from app.services.chunking import split_lines
from app.services.embeddings.base import EmbeddingError
from app.services.embeddings.registry import get_provider
from app.services.indexing import manifest, vector_store
from app.services.indexing.documents import (
    EMBEDDING_INPUT_VERSION,
    ChunkRef,
    embed_hash,
    embedding_text,
    keyword_fields,
    record_id,
)
from app.services.indexing.status import FAILED, INDEXING, READY
from app.services.ingestion import IngestionError, read_project_file

logger = logging.getLogger(__name__)

_running: set[str] = set()
_running_lock = threading.Lock()


class IndexingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class IndexingTimeout(Exception):
    pass


@dataclass
class _Pending:
    rid: str
    ref: ChunkRef
    text: str
    embed_hash: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _metadata(project: Project, ref: ChunkRef, model: str) -> dict:
    # ChromaDB metadata values cannot be None.
    return {
        "project_id": project.id,
        "path": ref.path,
        "language": ref.language,
        "symbol": ref.symbol or "",
        "symbol_kind": ref.symbol_kind or "",
        "chunk_kind": ref.chunk_kind,
        "start_line": ref.start_line,
        "end_line": ref.end_line,
        "content_hash": ref.content_hash,
        "analysis_revision": project.analysis_revision,
        "embedding_model": model,
        "input_version": EMBEDDING_INPUT_VERSION,
    }


def _current_refs(session: Session, project_id: str) -> list[ChunkRef]:
    symbol_kinds: dict[tuple[str, str], str] = {}
    for path, qualified, kind in session.execute(
        select(CodeSymbol.path, CodeSymbol.qualified_name, CodeSymbol.kind)
        .where(CodeSymbol.project_id == project_id)
        .order_by(CodeSymbol.id)
    ):
        symbol_kinds.setdefault((path, qualified), kind)
    rows = session.execute(
        select(
            CodeChunk.path, CodeChunk.start_line, CodeChunk.end_line, CodeChunk.kind,
            CodeChunk.symbol, CodeChunk.language, CodeChunk.content_hash,
        )
        .where(CodeChunk.project_id == project_id)
        .order_by(CodeChunk.path, CodeChunk.chunk_index)
    ).all()
    return [
        ChunkRef(
            path=r.path, start_line=r.start_line, end_line=r.end_line, chunk_kind=r.kind,
            symbol=r.symbol, symbol_kind=symbol_kinds.get((r.path, r.symbol or "")),
            language=r.language, content_hash=r.content_hash,
        )
        for r in rows
    ]


def _chunk_texts(settings: Settings, project_id: str, refs: list[ChunkRef]):
    """Yield (ref, text) for refs whose source still matches the analyzed hash; one file is
    read at a time. Mismatching or missing sources yield (ref, None)."""
    by_path: dict[str, list[ChunkRef]] = defaultdict(list)
    for ref in refs:
        by_path[ref.path].append(ref)
    for path, file_refs in by_path.items():
        try:
            lines = split_lines(read_project_file(settings, project_id, path))
        except (IngestionError, OSError):
            lines = None
        for ref in file_refs:
            if lines is None:
                yield ref, None
                continue
            text = "".join(lines[ref.start_line - 1 : ref.end_line])
            yield ref, (text if _hash(text) == ref.content_hash else None)


def index_project(session: Session, settings: Settings, project_id: str) -> SearchIndex:
    project = session.get(Project, project_id)
    if project is None:
        raise IndexingError("Project not found.", 404)
    if project.analysis_status != "ready":
        raise IndexingError("Run code analysis before indexing this project.", 409)
    with _running_lock:
        if project_id in _running:
            raise IndexingError("Indexing is already running for this project.", 409)
        _running.add(project_id)
    try:
        return _run(session, settings, project)
    finally:
        with _running_lock:
            _running.discard(project_id)


def _run(session: Session, settings: Settings, project: Project) -> SearchIndex:
    project_id = project.id
    started = time.monotonic()
    state = session.get(SearchIndex, project_id)
    if state is None:
        state = SearchIndex(project_id=project_id)
        session.add(state)
    previous_collection = state.collection_name
    state.status = INDEXING
    state.started_at = _now()
    state.error = None
    state.total_chunks = state.processed_chunks = 0
    state.embedded_count = state.reused_count = state.removed_count = state.skipped_count = 0
    session.commit()

    try:
        _build(session, settings, project, state, previous_collection, started)
        return state
    except Exception as exc:
        session.rollback()
        if isinstance(exc, IndexingTimeout):
            message = (
                f"Indexing exceeded the {settings.index_timeout_seconds:.0f} s limit "
                "(INDEX_TIMEOUT_SECONDS); the previous index was kept."
            )
        elif isinstance(exc, (EmbeddingError, IndexingError, vector_store.VectorStoreError)):
            message = str(exc)
        else:
            logger.exception("Indexing failed for project %s", project_id)
            message = f"Indexing failed ({type(exc).__name__}); the previous index was kept."
        current = session.get(SearchIndex, project_id)
        if current is None:  # project deleted meanwhile
            raise IndexingError("Project was deleted during indexing.", 404) from exc
        current.status = FAILED
        current.error = message[:1000]
        current.duration_ms = int((time.monotonic() - started) * 1000)
        session.commit()
        return current


def _build(
    session: Session,
    settings: Settings,
    project: Project,
    state: SearchIndex,
    previous_collection: str | None,
    started: float,
) -> None:
    deadline = started + settings.index_timeout_seconds
    revision = project.analysis_revision
    refs = _current_refs(session, project.id)
    if len(refs) > settings.index_max_chunks:
        raise IndexingError(
            f"Project has {len(refs)} chunks; the indexing limit is {settings.index_max_chunks} "
            "(INDEX_MAX_CHUNKS)."
        )

    provider = get_provider(settings)
    model = provider.model_identifier()
    target = vector_store.collection_name(
        project.id, provider.name, model, provider.embedding_dimension(), EMBEDDING_INPUT_VERSION
    )
    try:
        vector_store.delete_orphan_collections(
            settings, set(session.scalars(select(Project.id)).all())
        )
    except Exception:  # housekeeping only
        logger.warning("Orphan collection cleanup failed", exc_info=True)
    collection = vector_store.VectorCollection(settings, target)

    # 1. Reconcile ChromaDB with the manifest.
    stored_ids = collection.all_ids()
    entries = manifest.load_entries(session, project.id)
    same_collection = previous_collection == target
    usable = {
        e.record_id: e for e in entries if same_collection and e.record_id in stored_ids
    }
    vector_by_hash = {e.embed_hash: e.record_id for e in usable.values()}

    # 2. Diff.
    desired: dict[str, ChunkRef] = {}
    for ref in refs:
        desired.setdefault(record_id(project.id, ref), ref)
    keep = desired.keys() & usable.keys()
    to_add = [(rid, ref) for rid, ref in desired.items() if rid not in keep]
    to_remove = [e for e in entries if e.record_id not in keep]

    state.total_chunks = len(desired)
    state.processed_chunks = len(keep)
    session.commit()

    # Kept vectors get current metadata (analysis revision) without re-embedding.
    kept = sorted(keep)
    for start in range(0, len(kept), 500):
        batch = kept[start : start + 500]
        collection.update_metadata(batch, [_metadata(project, desired[r], model) for r in batch])

    # 3. Embed and store new vectors in batches.
    batch_size = max(1, settings.embedding_batch_size)
    added: dict[str, tuple[ChunkRef, str]] = {}
    written: set[str] = set()
    pending: list[_Pending] = []

    def flush() -> None:
        reuse_from = {
            p.rid: (p.rid if p.rid in stored_ids else vector_by_hash.get(p.embed_hash))
            for p in pending
        }
        reused = collection.get_embeddings([src for src in reuse_from.values() if src])
        vectors: dict[str, list[float]] = {}
        to_embed = []
        for p in pending:
            source = reuse_from[p.rid]
            if source and source in reused:
                vectors[p.rid] = reused[source]
            else:
                to_embed.append(p)
        if to_embed:
            for p, vec in zip(to_embed, provider.embed_documents([p.text for p in to_embed])):
                vectors[p.rid] = vec
        ids = [p.rid for p in pending]
        collection.upsert(
            ids, [vectors[r] for r in ids], [_metadata(project, p.ref, model) for p in pending]
        )
        written.update(ids)
        state.embedded_count += len(to_embed)
        state.reused_count += len(pending) - len(to_embed)
        state.processed_chunks += len(pending)
        session.commit()
        pending.clear()
        if time.monotonic() > deadline:
            raise IndexingTimeout()

    for (rid, ref), (_, text) in zip(
        to_add, _chunk_texts(settings, project.id, [ref for _, ref in to_add])
    ):
        if text is None:
            state.skipped_count += 1  # source changed or missing since analysis
            continue
        doc = embedding_text(ref, text, settings.embedding_max_input_chars)
        pending.append(_Pending(rid, ref, doc, embed_hash(doc)))
        added[rid] = (ref, embed_hash(doc))
        if len(pending) >= batch_size:
            flush()
    if pending:
        flush()

    # 4. Swap the manifest and keyword index in one SQLite transaction.
    if session.get(Project, project.id) is None:
        raise IndexingError("Project was deleted during indexing.", 404)
    manifest.delete_entries(session, [e.id for e in to_remove])
    session.flush()
    added_refs = [ref for ref, _ in added.values()]
    entry_by_rid: dict[str, IndexEntry] = {}
    for rid, (ref, ehash) in added.items():
        entry = IndexEntry(
            project_id=project.id, record_id=rid, embed_hash=ehash, path=ref.path,
            start_line=ref.start_line, end_line=ref.end_line, chunk_kind=ref.chunk_kind,
            symbol=ref.symbol, symbol_kind=ref.symbol_kind, language=ref.language,
            content_hash=ref.content_hash,
        )
        session.add(entry)
        entry_by_rid[rid] = entry
    session.flush()
    rid_by_ref = {ref: rid for rid, (ref, _) in added.items()}
    for ref, text in _chunk_texts(settings, project.id, added_refs):
        if text is None:
            raise IndexingError("Source files changed while indexing; run indexing again.")
        path_terms, symbol_terms, body = keyword_fields(ref, text)
        entry = entry_by_rid[rid_by_ref[ref]]
        manifest.add_fts(session, entry.id, project.id, path_terms, symbol_terms, body)

    final_ids = set(keep) | set(added)
    state.status = READY
    state.embedding_provider = provider.name
    state.embedding_model = model
    state.embedding_dimension = provider.embedding_dimension()
    state.input_version = EMBEDDING_INPUT_VERSION
    state.collection_name = target
    state.analysis_revision = revision
    state.chunk_count = len(final_ids)
    state.removed_count = len(to_remove)
    state.indexed_at = _now()
    state.duration_ms = int((time.monotonic() - started) * 1000)
    state.error = None
    session.commit()

    # 5. Remove obsolete vectors only after the manifest no longer references them.
    try:
        obsolete = (stored_ids | written) - final_ids
        if obsolete:
            collection.delete(obsolete)
        if previous_collection and previous_collection != target:
            vector_store.delete_collections(settings, project.id, keep=target)
    except Exception:
        logger.warning("Vector cleanup for project %s deferred to the next run", project.id)


INTERRUPTED_ERROR = (
    "Indexing was interrupted (the server stopped before it finished). The previous index, if "
    "any, is unchanged and still searchable. Run indexing again."
)


def recover_interrupted_runs(session: Session) -> int:
    """Mark runs left in ``indexing`` by a stopped server as failed. Indexing only runs inside
    the API process, so at startup no run can still be in progress. Their partial vector writes
    are reconciled by the next run. Returns the number of recovered projects."""
    states = session.scalars(select(SearchIndex).where(SearchIndex.status == INDEXING)).all()
    for state in states:
        state.status = FAILED
        state.error = INTERRUPTED_ERROR
    session.commit()
    return len(states)


def drop_project_vectors(settings: Settings, project_id: str) -> None:
    """Delete a project's vector collections. Call after the SQLite deletion committed; a
    failure only leaves an unreachable collection that the next indexing run removes."""
    try:
        vector_store.delete_collections(settings, project_id)
    except Exception:
        logger.warning("Could not delete vector collections of project %s", project_id)
