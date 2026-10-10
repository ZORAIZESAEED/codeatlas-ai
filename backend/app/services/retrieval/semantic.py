"""Semantic retrieval from a project's ChromaDB collection.

The query is embedded with the provider and model recorded on the project's index (not the
currently configured one), so query and document vectors always come from the same model.
Distances are a retrieval signal, not a calibrated confidence.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import IndexEntry, SearchIndex
from app.services.embeddings.base import EmbeddingError
from app.services.embeddings.registry import get_provider
from app.services.indexing.vector_store import (
    VectorCollection,
    VectorStoreError,
    collection_exists,
)
from app.services.retrieval.models import Hit, SearchFilters, SemanticUnavailable, apply_filters


def semantic_search(
    session: Session,
    settings: Settings,
    state: SearchIndex | None,
    query: str,
    filters: SearchFilters,
    limit: int,
) -> tuple[list[Hit], int]:
    """Returns (hits, dropped) where ``dropped`` counts vectors without a manifest entry."""
    if state is None or not state.collection_name or not state.embedding_model:
        raise SemanticUnavailable("The project has no semantic index yet.")
    if not state.chunk_count:
        return [], 0
    try:
        provider = get_provider(settings, state.embedding_provider, state.embedding_model)
        if provider.embedding_dimension() != state.embedding_dimension:
            raise SemanticUnavailable("The embedding model dimension changed; reindex the project.")
        vector = provider.embed_query(query)
    except EmbeddingError as exc:
        raise SemanticUnavailable(str(exc)) from exc
    if not collection_exists(settings, state.collection_name):
        raise SemanticUnavailable("The vector collection is missing; reindex the project.")

    project_id = state.project_id
    ids = None
    if filters.active:
        ids = list(
            session.scalars(
                apply_filters(
                    select(IndexEntry.record_id).where(IndexEntry.project_id == project_id),
                    filters,
                )
            ).all()
        )
        if not ids:
            return [], 0
    try:
        collection = VectorCollection(settings, state.collection_name, create=False)
        matches = collection.query(vector, min(limit, state.chunk_count), ids)
    except VectorStoreError as exc:
        raise SemanticUnavailable(str(exc)) from exc
    except Exception as exc:
        raise SemanticUnavailable(f"Vector search failed ({type(exc).__name__}).") from exc

    by_record = dict(
        session.execute(
            select(IndexEntry.record_id, IndexEntry.id).where(
                IndexEntry.project_id == project_id,
                IndexEntry.record_id.in_([rid for rid, _ in matches]),
            )
        ).all()
    )
    hits: list[Hit] = []
    for rid, distance in matches:
        entry_id = by_record.get(rid)
        if entry_id is not None:  # unknown ids are stale vectors pending cleanup
            hits.append(Hit(entry_id=entry_id, rank=len(hits) + 1, score=distance))
    return hits, len(matches) - len(hits)
