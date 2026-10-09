from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

MB = 1024 * 1024


class Settings(BaseSettings):
    app_name: str = "CodeAtlas AI"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/codeatlas.db"
    frontend_origin: str = "http://localhost:3000"

    # Repository ingestion. Each project is stored in `<workspace_dir>/<project_id>`.
    workspace_dir: str = "./data/workspaces"
    max_upload_bytes: int = 20 * MB
    max_download_bytes: int = 50 * MB
    max_extracted_bytes: int = 200 * MB
    max_archive_entries: int = 20_000
    max_indexed_file_bytes: int = 1 * MB
    github_timeout_seconds: float = 30.0

    # Code analysis chunk limits. A single line longer than the char limit is never split.
    chunk_max_lines: int = 100
    chunk_max_chars: int = 4000
    # Structural parsing limits; larger files (or files past the per-project budget) are
    # still chunked, by lines.
    parse_max_file_bytes: int = 512 * 1024
    parse_max_total_bytes: int = 50 * MB

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
