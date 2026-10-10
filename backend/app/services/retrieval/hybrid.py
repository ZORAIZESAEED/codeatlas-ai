"""Search pipeline: normalize -> exact + keyword + semantic retrieval -> RRF fusion ->
source validation and deduplication -> optional reranking -> results.

Modes: ``keyword`` (exact + FTS5), ``semantic`` (vectors only) and ``hybrid`` (all three).
If semantic retrieval is unavailable, ``semantic`` mode fails with a clear error and ``hybrid``
falls back to keyword retrieval, reported as ``mode_used="keyword"`` with a fallback reason.
"""
import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import IndexEntry, Project, ProjectFile, SearchIndex
from app.services.chunking import split_lines
from app.services.indexing.status import (
    FAILED,
    INDEXING,
    STALE,
    EffectiveStatus,
    effective_status,
)
from app.services.ingestion import IngestionError, read_project_file
from app.services.retrieval.fusion import reciprocal_rank_fusion
from app.services.retrieval.keyword import exact_search, keyword_search
from app.services.retrieval.models import (
    EXACT,
    KEYWORD,
    SEMANTIC,
    Candidate,
    Hit,
    SearchError,
    SearchFilters,
    SemanticUnavailable,
)
from app.services.retrieval.reranker import FeatureReranker, Reranker
from app.services.retrieval.semantic import semantic_search
from app.services.retrieval.terms import identifier_candidates, is_identifier_like

MODES = ("keyword", "semantic", "hybrid")


@dataclass
class SearchOutcome:
    project: Project
    query: str
    mode_requested: str
    mode_used: str
    status: EffectiveStatus
    state: SearchIndex | None
    results: list[Candidate]
    reranked: bool
    fallback_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    candidates: dict[str, int] = field(default_factory=dict)
    fusion_weights: dict[str, float] = field(default_factory=dict)


def normalize_query(query: str) -> str:
    return " ".join("".join(ch for ch in query if ch.isprintable()).split())


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def fusion_weights(settings: Settings, query: str) -> dict[str, float]:
    """Per-query RRF weights. BM25 keeps full weight when the query names code
    (``create_access_token``, ``AuthService``, ``core/db.py``); for plain questions its matches
    on everyday words are noisy, so it counts less than semantic similarity."""
    names_code = any(is_identifier_like(t) for t in identifier_candidates(query))
    return {
        EXACT: settings.rrf_weight_exact,
        KEYWORD: settings.rrf_weight_keyword if names_code else settings.rrf_weight_keyword_natural,
        SEMANTIC: settings.rrf_weight_semantic,
    }


def _validate_sources(
    session: Session, settings: Settings, project_id: str, candidates: list[Candidate]
) -> tuple[list[Candidate], int]:
    """Keep candidates whose file is a known project file and whose current source still hashes
    to the indexed content. Attaches the chunk text. Returns (valid, dropped count)."""
    paths = {c.entry.path for c in candidates}
    known = set(
        session.scalars(
            select(ProjectFile.path).where(
                ProjectFile.project_id == project_id, ProjectFile.path.in_(paths)
            )
        ).all()
    )
    lines_by_path: dict[str, list[str] | None] = {}
    valid: list[Candidate] = []
    for c in candidates:
        path = c.entry.path
        if path not in lines_by_path:
            try:
                lines_by_path[path] = (
                    split_lines(read_project_file(settings, project_id, path))
                    if path in known else None
                )
            except (IngestionError, OSError):
                lines_by_path[path] = None
        lines = lines_by_path[path]
        if lines is None or c.entry.end_line > len(lines):
            continue
        text = "".join(lines[c.entry.start_line - 1 : c.entry.end_line])
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != c.entry.content_hash:
            continue
        c.text = text
        valid.append(c)
    return valid, len(candidates) - len(valid)


def _drop_overlaps(candidates: list[Candidate]) -> list[Candidate]:
    """Keep the best-ranked of any results whose line ranges overlap in the same file."""
    kept: list[Candidate] = []
    ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for c in candidates:
        span = (c.entry.start_line, c.entry.end_line)
        if any(s <= span[1] and span[0] <= e for s, e in ranges[c.entry.path]):
            continue
        ranges[c.entry.path].append(span)
        kept.append(c)
    return kept


def search(
    session: Session,
    settings: Settings,
    project_id: str,
    query: str,
    mode: str = "hybrid",
    limit: int = 10,
    filters: SearchFilters | None = None,
    rerank: bool | None = None,
    reranker: Reranker | None = None,
) -> SearchOutcome:
    started = time.perf_counter()
    filters = filters or SearchFilters()
    if mode not in MODES:
        raise SearchError(f"Unknown search mode '{mode}'.", 422)
    query = normalize_query(query)
    if not query:
        raise SearchError("The search query is empty.", 422)
    limit = max(1, min(limit, settings.search_max_limit))
    if rerank is None:
        rerank = settings.search_rerank_default

    project = session.get(Project, project_id)
    if project is None:
        raise SearchError("Project not found.", 404)
    state = session.get(SearchIndex, project_id)
    status = effective_status(project, state, settings)
    entry_count = session.scalar(
        select(func.count()).select_from(IndexEntry).where(IndexEntry.project_id == project_id)
    ) or 0
    completed = state is not None and state.indexed_at is not None
    if not entry_count and not completed:  # a completed index may legitimately be empty
        if status.status == INDEXING:
            raise SearchError("Indexing is in progress; search is available when it finishes.", 409)
        if status.status == FAILED:
            raise SearchError(
                f"Indexing failed and no earlier index exists: {status.error}", 409
            )
        raise SearchError(
            "This project has not been indexed yet. Build the search index first.", 409
        )

    outcome = SearchOutcome(
        project=project, query=query, mode_requested=mode, mode_used=mode, status=status,
        state=state, results=[], reranked=False,
    )
    stale = " ".join(status.stale_reasons)
    if status.status == STALE:
        outcome.warnings.append("The search index is stale: " + stale)
    elif status.status == FAILED:
        outcome.warnings.append(
            "The last indexing run failed; results come from the previous complete index"
            + (f", which is also stale: {stale}" if stale else ".")
        )
    elif status.status == INDEXING:
        outcome.warnings.append(
            "Indexing is in progress; results come from the previous complete index."
        )

    pool = max(settings.search_candidate_pool, limit)
    lists: dict[str, list[Hit]] = {}
    if mode in ("keyword", "hybrid"):
        t = time.perf_counter()
        lists[EXACT] = exact_search(session, project_id, query, filters, pool)
        outcome.timings_ms["exact"] = _ms(t)
        t = time.perf_counter()
        lists[KEYWORD] = keyword_search(session, project_id, query, filters, pool)
        outcome.timings_ms["keyword"] = _ms(t)
    if mode in ("semantic", "hybrid"):
        t = time.perf_counter()
        try:
            hits, dropped = semantic_search(session, settings, state, query, filters, pool)
            lists[SEMANTIC] = hits
            if dropped:
                outcome.candidates["semantic_unreferenced"] = dropped
        except SemanticUnavailable as exc:
            if mode == "semantic":
                raise SearchError(f"Semantic search is unavailable: {exc}", 503) from exc
            outcome.mode_used = "keyword"
            outcome.fallback_reason = f"Semantic search unavailable: {exc}"
            outcome.warnings.append(
                "Semantic search was unavailable, so only keyword retrieval ran."
            )
        outcome.timings_ms["semantic"] = _ms(t)
    outcome.candidates.update({m: len(h) for m, h in lists.items()})

    t = time.perf_counter()
    weights = fusion_weights(settings, query)
    outcome.fusion_weights = {m: weights[m] for m in lists}
    fused = reciprocal_rank_fusion(lists, weights, settings.rrf_k)[:pool]
    entries = {
        e.id: e
        for e in session.scalars(
            select(IndexEntry).where(
                IndexEntry.project_id == project_id,
                IndexEntry.id.in_([entry_id for entry_id, *_ in fused]),
            )
        ).all()
    }
    candidates = [
        Candidate(entry=entries[eid], fusion_score=score, ranks=ranks, raw_scores=raw, score=score)
        for eid, score, ranks, raw in fused
        if eid in entries
    ]
    outcome.timings_ms["fusion"] = _ms(t)

    t = time.perf_counter()
    candidates, invalid = _validate_sources(session, settings, project_id, candidates)
    if invalid:
        outcome.candidates["invalid_source"] = invalid
        outcome.warnings.append(
            f"{invalid} indexed chunk(s) no longer match the project source and were skipped."
        )
    candidates = _drop_overlaps(candidates)
    outcome.timings_ms["validation"] = _ms(t)

    if rerank and candidates:
        t = time.perf_counter()
        candidates = (reranker or FeatureReranker()).rerank(query, candidates)
        outcome.reranked = True
        outcome.timings_ms["rerank"] = _ms(t)
    outcome.results = candidates[:limit]
    outcome.timings_ms["total"] = _ms(started)
    return outcome
