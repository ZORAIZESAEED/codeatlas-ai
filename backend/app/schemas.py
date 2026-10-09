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

    @field_validator("created_at")
    @classmethod
    def assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops timezone info; stored values are always UTC.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class ProjectFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: str
    path: str
    language: str
    size_bytes: int
    line_count: int
    content_hash: str


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


class ProjectOverview(BaseModel):
    project: ProjectSummary
    languages: list[LanguageStat]
    directory_count: int
    top_level: list[TopLevelEntry]
    largest_files: list[ProjectFileOut]


class GitHubImportRequest(BaseModel):
    url: str = Field(min_length=1, max_length=300, examples=["https://github.com/owner/repo"])
