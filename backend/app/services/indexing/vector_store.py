"""ChromaDB persistence for chunk vectors.

One collection per (project, provider, model, dimension, input version): project isolation is
structural (a query only ever touches one project's collection) and vectors from different
models or dimensions can never be mixed. Record ids are deterministic (see documents.record_id).
"""
import hashlib
import logging
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path

from app.core.config import Settings

logger = logging.getLogger(__name__)

COLLECTION_PREFIX = "ca_"
WRITE_BATCH = 500  # keeps each ChromaDB call (and its memory) bounded

_clients: dict[str, object] = {}
_lock = threading.Lock()


class VectorStoreError(RuntimeError):
    pass


def _client(settings: Settings):
    path = str(Path(settings.chroma_dir).resolve())
    with _lock:
        client = _clients.get(path)
        if client is None:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            Path(path).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=path,
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
            )
            _clients[path] = client
        return client


def project_prefix(project_id: str) -> str:
    return f"{COLLECTION_PREFIX}{project_id.replace('-', '').lower()}_"


def collection_name(
    project_id: str, provider: str, model: str, dimension: int, input_version: str
) -> str:
    signature = hashlib.sha256(
        f"{provider}\x00{model}\x00{dimension}\x00{input_version}".encode()
    ).hexdigest()[:12]
    return project_prefix(project_id) + signature


def _batches(items: Sequence, size: int = WRITE_BATCH) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _names(client) -> list[str]:
    return [c if isinstance(c, str) else c.name for c in client.list_collections()]


class VectorCollection:
    def __init__(self, settings: Settings, name: str, create: bool = True):
        client = _client(settings)
        try:
            if create:
                self._col = client.get_or_create_collection(
                    name, metadata={"hnsw:space": "cosine"}, embedding_function=None
                )
            else:
                self._col = client.get_collection(name, embedding_function=None)
        except Exception as exc:
            raise VectorStoreError(
                f"Vector collection unavailable ({type(exc).__name__})."
            ) from exc

    def count(self) -> int:
        return self._col.count()

    def all_ids(self) -> set[str]:
        ids: set[str] = set()
        offset = 0
        while True:
            page = self._col.get(include=[], limit=5000, offset=offset)["ids"]
            ids.update(page)
            if len(page) < 5000:
                return ids
            offset += len(page)

    def get_embeddings(self, ids: Sequence[str]) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        for batch in _batches(list(ids)):
            result = self._col.get(ids=list(batch), include=["embeddings"])
            for rid, vector in zip(result["ids"], result["embeddings"]):
                found[rid] = [float(x) for x in vector]
        return found

    def upsert(self, ids: list[str], embeddings: list[list[float]], metadatas: list[dict]) -> None:
        for start in range(0, len(ids), WRITE_BATCH):
            end = start + WRITE_BATCH
            self._col.upsert(
                ids=ids[start:end], embeddings=embeddings[start:end], metadatas=metadatas[start:end]
            )

    def update_metadata(self, ids: list[str], metadatas: list[dict]) -> None:
        for start in range(0, len(ids), WRITE_BATCH):
            end = start + WRITE_BATCH
            self._col.update(ids=ids[start:end], metadatas=metadatas[start:end])

    def delete(self, ids: Iterable[str]) -> None:
        for batch in _batches(list(ids)):
            self._col.delete(ids=list(batch))

    def query(
        self, vector: list[float], n_results: int, ids: list[str] | None = None
    ) -> list[tuple[str, float]]:
        """Nearest records as (record_id, cosine distance), closest first."""
        if n_results <= 0 or (ids is not None and not ids):
            return []
        result = self._col.query(
            query_embeddings=[vector], n_results=n_results, ids=ids, include=["distances"]
        )
        return list(zip(result["ids"][0], (float(d) for d in result["distances"][0])))


def collection_exists(settings: Settings, name: str) -> bool:
    if not Path(settings.chroma_dir).exists():
        return False
    return name in _names(_client(settings))


def delete_collections(settings: Settings, project_id: str, keep: str | None = None) -> int:
    """Delete a project's collections (all, or all but ``keep``). Returns the number deleted."""
    if not Path(settings.chroma_dir).exists():
        return 0  # nothing was ever indexed with this storage location
    client = _client(settings)
    prefix, deleted = project_prefix(project_id), 0
    for name in _names(client):
        if name.startswith(prefix) and name != keep:
            client.delete_collection(name)
            deleted += 1
    return deleted


def delete_orphan_collections(settings: Settings, live_project_ids: set[str]) -> int:
    """Remove collections of projects that no longer exist (e.g. vector cleanup failed after
    a project was deleted)."""
    if not Path(settings.chroma_dir).exists():
        return 0
    client = _client(settings)
    live = {project_prefix(pid) for pid in live_project_ids}
    deleted = 0
    for name in _names(client):
        if not name.startswith(COLLECTION_PREFIX):
            continue
        prefix = name[: name.rfind("_") + 1]
        if prefix not in live:
            client.delete_collection(name)
            deleted += 1
    return deleted
