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
