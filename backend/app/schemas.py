from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    source_type: str
    source_url: str | None
    status: str
    created_at: datetime
    file_count: int
    total_lines: int
    total_bytes: int
    skipped_count: int
    languages: list[str] = []
    analysis_status: str = "pending"
    analyzed_at: datetime | None = None
    symbol_count: int = 0
    chunk_count: int = 0

    @field_validator("created_at", "analyzed_at")
    @classmethod
    def assume_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops timezone info; stored values are always UTC.
        if value is None or value.tzinfo:
            return value
        return value.replace(tzinfo=timezone.utc)


class ProjectFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    path: str
    language: str
    size_bytes: int
    line_count: int
    content_hash: str
    parser: str | None = None
    parse_error: str | None = None


class FileListResponse(BaseModel):
    project_id: str
    files: list[ProjectFileOut]


class FileContentResponse(BaseModel):
    project_id: str
    path: str
    language: str
    size_bytes: int
    line_count: int
    content: str


class LanguageStat(BaseModel):
    language: str
    files: int
    lines: int
    bytes: int


class TopLevelEntry(BaseModel):
    name: str
    type: Literal["file", "directory"]
    file_count: int


class FileParseIssue(BaseModel):
    path: str
    parser: str | None
    parse_error: str


class AnalysisOverview(BaseModel):
    status: str
    analyzed_at: datetime | None
    symbol_count: int
    chunk_count: int
    symbols_by_kind: dict[str, int]
    files_by_parser: dict[str, int]
    issue_count: int
    issues: list[FileParseIssue]


class ProjectOverview(BaseModel):
    project: ProjectSummary
    languages: list[LanguageStat]
    directory_count: int
    top_level: list[TopLevelEntry]
    largest_files: list[ProjectFileOut]
    analysis: AnalysisOverview


class SymbolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    name: str
    qualified_name: str
    kind: str
    parent: str | None
    start_line: int
    end_line: int
    signature: str | None
    docstring: str | None


class SymbolListResponse(BaseModel):
    project_id: str
    total: int
    symbols: list[SymbolOut]


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    chunk_index: int
    start_line: int
    end_line: int
    kind: str
    symbol: str | None
    language: str
    char_count: int
    content_hash: str
    content: str | None = None


class ChunkListResponse(BaseModel):
    project_id: str
    total: int
    chunks: list[ChunkOut]


class GitHubImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=300, examples=["https://github.com/owner/repo"])


class IndexStatusOut(BaseModel):
    project_id: str
    # pending | indexing | ready | stale | failed
    status: str
    stale_reasons: list[str] = []
    error: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    input_version: str | None = None
    configured_provider: str
    configured_model: str
    indexed_chunk_count: int = 0
    analysis_chunk_count: int = 0
    analysis_revision: int = 0
    indexed_analysis_revision: int | None = None
    # Counters of the last (or running) indexing run.
    total_chunks: int = 0
    processed_chunks: int = 0
    embedded_count: int = 0
    reused_count: int = 0
    removed_count: int = 0
    skipped_count: int = 0
    started_at: datetime | None = None
    last_indexed_at: datetime | None = None
    duration_ms: int | None = None


def _normalize_path_filter(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().replace("\\", "/").strip("/")
    while value.startswith("./"):
        value = value[2:]
    if not value:
        return None
    if any(not ch.isprintable() for ch in value) or ".." in value.split("/"):
        raise ValueError("Invalid path filter.")
    return value


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    limit: int = Field(default=10, ge=1, le=50)
    language: str | None = Field(default=None, max_length=30)
    file_path: str | None = Field(default=None, max_length=1024)
    directory: str | None = Field(default=None, max_length=1024)
    symbol_type: str | None = Field(default=None, max_length=20)
    # None = server default (SEARCH_RERANK_DEFAULT, off).
    rerank: bool | None = None

    @field_validator("file_path", "directory")
    @classmethod
    def normalize_path(cls, value: str | None) -> str | None:
        return _normalize_path_filter(value)

    @field_validator("language", "symbol_type")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class SearchResultOut(BaseModel):
    chunk_id: str
    rank: int
    file_path: str
    symbol_name: str | None
    symbol_type: str | None
    chunk_kind: str
    language: str
    start_line: int
    end_line: int
    snippet: str
    snippet_truncated: bool
    retrieval_methods: list[str]
    # Ranking score used to order results (RRF, optionally reranked). Not a confidence.
    score: float
    fusion_score: float
    method_ranks: dict[str, int]
    raw_scores: dict[str, float]
    rerank_features: dict[str, float] = {}


class Citation(BaseModel):
    rank: int
    file_path: str
    start_line: int
    end_line: int
    reference: str  # "path:start-end"


class SearchResponse(BaseModel):
    project_id: str
    query: str
    mode_requested: str
    mode_used: str
    fallback_reason: str | None = None
    reranked: bool
    # RRF weight applied to each retriever that ran for this query.
    fusion_weights: dict[str, float] = {}
    index_status: IndexStatusOut
    results: list[SearchResultOut]
    citations: list[Citation]
    warnings: list[str] = []
    candidate_counts: dict[str, int] = {}
    timings_ms: dict[str, float] = {}


class LLMCapabilitiesOut(BaseModel):
    streaming: bool
    native_tools: Literal["no", "advertised", "yes"]
    json_output: bool
    usage: bool


class LLMStatusOut(BaseModel):
    provider: str
    model: str | None
    available: bool
    message: str
    capabilities: LLMCapabilitiesOut | None = None
    context_length: int | None = None
    configured_context_tokens: int
    installed_models: list[str] = []
    sends_code_off_machine: bool = False


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationOut(BaseModel):
    id: str
    project_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    @field_validator("created_at", "updated_at")
    @classmethod
    def assume_utc(cls, value: datetime) -> datetime:
        return ProjectSummary.assume_utc(value)


class MessageCitationOut(BaseModel):
    evidence_id: str
    file_id: int | None
    chunk_id: str | None
    file_path: str
    start_line: int
    end_line: int
    symbol: str | None
    source_revision: str
    reference: str


class InvestigationRunOut(BaseModel):
    mode: str
    planner: str | None
    status: str
    tool_call_count: int
    llm_request_count: int
    evidence_count: int
    duration_ms: int | None
    trace: list[dict] = []


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    status: str
    mode: str | None = None
    provider: str | None = None
    model: str | None = None
    error: str | None = None
    warnings: list[str] = []
    input_tokens: int | None = None
    output_tokens: int | None = None
    analysis_revision: int | None = None
    created_at: datetime
    completed_at: datetime | None = None
    citations: list[MessageCitationOut] = []
    run: InvestigationRunOut | None = None

    @field_validator("created_at", "completed_at")
    @classmethod
    def assume_utc(cls, value: datetime | None) -> datetime | None:
        return ProjectSummary.assume_utc(value)


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[ChatMessageOut]
    project_analysis_revision: int
    # Earlier answers were based on an older analysis of the project.
    stale: bool


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)
    mode: Literal["baseline", "investigation"] = "investigation"
    retrieval_mode: Literal["hybrid", "keyword", "semantic"] | None = None
    file_paths: list[str] = Field(default_factory=list, max_length=5)


class ChatResponse(BaseModel):
    conversation: ConversationOut
    message: ChatMessageOut
