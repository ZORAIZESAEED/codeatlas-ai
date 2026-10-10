from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.projects import _get_project, get_settings_dep
from app.core.config import Settings
from app.db.models import Project, SearchIndex
from app.db.session import get_db
from app.schemas import (
    Citation,
    IndexStatusOut,
    SearchRequest,
    SearchResponse,
    SearchResultOut,
)
from app.services.indexing.indexer import IndexingError, index_project
from app.services.indexing.status import as_utc, effective_status
from app.services.retrieval.hybrid import search as run_search
from app.services.retrieval.models import SearchError, SearchFilters

router = APIRouter(prefix="/api/projects", tags=["search"])


def index_status_out(
    project: Project, state: SearchIndex | None, settings: Settings
) -> IndexStatusOut:
    effective = effective_status(project, state, settings)
    out = IndexStatusOut(
        project_id=project.id,
        status=effective.status,
        stale_reasons=effective.stale_reasons,
        error=effective.error,
        configured_provider=settings.embedding_provider,
        configured_model=settings.embedding_model,
        analysis_chunk_count=project.chunk_count,
        analysis_revision=project.analysis_revision,
    )
    if state is not None:
        out.embedding_provider = state.embedding_provider
        out.embedding_model = state.embedding_model
        out.embedding_dimension = state.embedding_dimension
        out.input_version = state.input_version
        out.indexed_chunk_count = state.chunk_count
        out.indexed_analysis_revision = state.analysis_revision
        out.total_chunks = state.total_chunks
        out.processed_chunks = state.processed_chunks
        out.embedded_count = state.embedded_count
        out.reused_count = state.reused_count
        out.removed_count = state.removed_count
        out.skipped_count = state.skipped_count
        out.started_at = as_utc(state.started_at)
        out.last_indexed_at = as_utc(state.indexed_at)
        out.duration_ms = state.duration_ms
    return out


@router.post("/{project_id}/index", response_model=IndexStatusOut)
def build_index(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> IndexStatusOut:
    """Build or incrementally update the project's search index. Runs synchronously: the
    request returns when indexing has finished (or failed). Poll ``index-status`` meanwhile."""
    project = _get_project(db, project_id)
    try:
        state = index_project(db, settings, project.id)
    except IndexingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db.refresh(project)
    return index_status_out(project, state, settings)


@router.get("/{project_id}/index-status", response_model=IndexStatusOut)
def get_index_status(
    project_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> IndexStatusOut:
    project = _get_project(db, project_id)
    return index_status_out(project, db.get(SearchIndex, project.id), settings)


@router.post("/{project_id}/search", response_model=SearchResponse)
def search_project(
    project_id: str,
    payload: SearchRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> SearchResponse:
    filters = SearchFilters(
        language=payload.language,
        file_path=payload.file_path,
        directory=payload.directory,
        symbol_type=payload.symbol_type,
    )
    try:
        outcome = run_search(
            db, settings, project_id, payload.query, mode=payload.mode, limit=payload.limit,
            filters=filters, rerank=payload.rerank,
        )
    except SearchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    results = []
    max_lines, max_chars = settings.search_snippet_max_lines, settings.search_snippet_max_chars
    for rank, c in enumerate(outcome.results, start=1):
        lines = c.text.splitlines(keepends=True)
        snippet = "".join(lines[:max_lines])[:max_chars]
        results.append(
            SearchResultOut(
                chunk_id=c.entry.record_id,
                rank=rank,
                file_path=c.entry.path,
                symbol_name=c.entry.symbol,
                symbol_type=c.entry.symbol_kind,
                chunk_kind=c.entry.chunk_kind,
                language=c.entry.language,
                start_line=c.entry.start_line,
                end_line=c.entry.end_line,
                snippet=snippet,
                snippet_truncated=len(snippet) < len(c.text),
                retrieval_methods=c.methods,
                score=round(c.score, 6),
                fusion_score=round(c.fusion_score, 6),
                method_ranks=c.ranks,
                raw_scores={k: round(v, 6) for k, v in c.raw_scores.items()},
                rerank_features=c.features,
            )
        )
    return SearchResponse(
        project_id=outcome.project.id,
        query=outcome.query,
        mode_requested=outcome.mode_requested,
        mode_used=outcome.mode_used,
        fallback_reason=outcome.fallback_reason,
        reranked=outcome.reranked,
        fusion_weights=outcome.fusion_weights,
        index_status=index_status_out(outcome.project, outcome.state, settings),
        results=results,
        citations=[
            Citation(
                rank=r.rank, file_path=r.file_path, start_line=r.start_line,
                end_line=r.end_line, reference=f"{r.file_path}:{r.start_line}-{r.end_line}",
            )
            for r in results
        ],
        warnings=outcome.warnings,
        candidate_counts=outcome.candidates,
        timings_ms=outcome.timings_ms,
    )
