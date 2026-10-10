"""Shared retrieval types and manifest filtering."""
from dataclasses import dataclass, field

from sqlalchemy import Select, select

from app.db.models import IndexEntry

EXACT, KEYWORD, SEMANTIC = "exact", "keyword", "semantic"
METHOD_ORDER = (EXACT, KEYWORD, SEMANTIC)


class SearchError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SemanticUnavailable(Exception):
    """Semantic retrieval cannot run (not indexed, model unavailable, collection missing)."""


@dataclass(frozen=True)
class SearchFilters:
    language: str | None = None
    file_path: str | None = None
    directory: str | None = None
    symbol_type: str | None = None

    @property
    def active(self) -> bool:
        return any((self.language, self.file_path, self.directory, self.symbol_type))


@dataclass
class Hit:
    """One retriever's result: manifest entry id, 1-based rank and the raw retriever score
    (BM25 for keyword, cosine distance for semantic, match strength for exact)."""

    entry_id: int
    rank: int
    score: float


@dataclass
class Candidate:
    entry: IndexEntry
    fusion_score: float
    ranks: dict[str, int] = field(default_factory=dict)
    raw_scores: dict[str, float] = field(default_factory=dict)
    score: float = 0.0  # final ranking score (fusion, possibly reranked); not a confidence
    features: dict[str, float] = field(default_factory=dict)
    text: str = ""

    @property
    def methods(self) -> list[str]:
        return [m for m in METHOD_ORDER if m in self.ranks]


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_filters(query: Select, filters: SearchFilters) -> Select:
    """Restrict a select over IndexEntry. Filters only compare against stored manifest values;
    they never touch the filesystem."""
    if filters.language:
        query = query.where(IndexEntry.language == filters.language)
    if filters.file_path:
        query = query.where(IndexEntry.path == filters.file_path)
    if filters.directory:
        query = query.where(
            IndexEntry.path.like(_like_escape(filters.directory) + "/%", escape="\\")
        )
    if filters.symbol_type:
        # A symbol kind (function, class, method, ...) or a chunk kind (module, text).
        query = query.where(
            (IndexEntry.symbol_kind == filters.symbol_type)
            | ((IndexEntry.symbol_kind.is_(None)) & (IndexEntry.chunk_kind == filters.symbol_type))
        )
    return query


def entries_query(project_id: str, filters: SearchFilters) -> Select:
    return apply_filters(select(IndexEntry).where(IndexEntry.project_id == project_id), filters)
