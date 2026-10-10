"""Effective index status.

Stored states are pending / indexing / ready / failed. "stale" is derived on read, so it can
never be missed: a ready index is stale when the project was re-analyzed after indexing or the
configured embedding model / input format differs from the one the index was built with.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.db.models import Project, SearchIndex
from app.services.indexing.documents import EMBEDDING_INPUT_VERSION

PENDING, INDEXING, READY, STALE, FAILED = "pending", "indexing", "ready", "stale", "failed"


@dataclass
class EffectiveStatus:
    status: str
    stale_reasons: list[str] = field(default_factory=list)
    error: str | None = None


def as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=timezone.utc)  # SQLite drops tz info; values are UTC


def effective_status(
    project: Project, state: SearchIndex | None, settings: Settings
) -> EffectiveStatus:
    if state is None or state.status == PENDING:
        return EffectiveStatus(PENDING)
    if state.status == INDEXING:
        started = as_utc(state.started_at)
        limit = timedelta(seconds=settings.index_timeout_seconds + 120)
        if started and datetime.now(timezone.utc) - started > limit:
            # Backstop for a stopped process; startup recovery normally reports this at once.
            return EffectiveStatus(
                FAILED, _stale_reasons(project, state, settings),
                error="Indexing was interrupted. Run indexing again.",
            )
        return EffectiveStatus(INDEXING)
    if state.status == FAILED:
        # The previous index (if any) is still searchable; say whether it is also outdated.
        return EffectiveStatus(FAILED, _stale_reasons(project, state, settings), state.error)
    reasons = _stale_reasons(project, state, settings)
    return EffectiveStatus(STALE if reasons else READY, reasons)


def _stale_reasons(project: Project, state: SearchIndex, settings: Settings) -> list[str]:
    if state.indexed_at is None:
        return []  # no completed index to be stale
    reasons = []
    if state.analysis_revision != project.analysis_revision:
        reasons.append("The project was re-analyzed after it was indexed.")
    if (state.embedding_provider, state.embedding_model) != (
        settings.embedding_provider, settings.embedding_model
    ):
        reasons.append(
            f"The configured embedding model ({settings.embedding_model}) differs from the "
            f"indexed one ({state.embedding_model})."
        )
    if state.input_version != EMBEDDING_INPUT_VERSION:
        reasons.append("The embedding input format changed since indexing.")
    return reasons
