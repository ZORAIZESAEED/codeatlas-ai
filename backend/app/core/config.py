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

    # Semantic indexing (Milestone 3). Models are downloaded on first use, never at startup.
    chroma_dir: str = "./data/chroma"
    embedding_provider: str = "fastembed"  # "fastembed" (local ONNX) | "fake" (tests only)
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Instruction prepended to queries (not documents). None = the model's documented default.
    embedding_query_prefix: str | None = None
    embedding_cache_dir: str = "./data/models"
    # Small batches measured ~3.5x faster than 32 on CPU for long code chunks (less padding).
    embedding_batch_size: int = 4
    embedding_threads: int | None = None
    # Embedding input is capped (header + start of the chunk); the keyword index sees it all.
    embedding_max_input_chars: int = 2000
    index_max_chunks: int = 50_000
    index_timeout_seconds: float = 1800.0

    # Retrieval. Weighted Reciprocal Rank Fusion: score = sum(weight / (rrf_k + rank)).
    search_max_limit: int = 50
    search_candidate_pool: int = 50
    rrf_k: int = 60
    rrf_weight_exact: float = 1.0
    # BM25 weight for queries containing identifier-like tokens (snake_case, camelCase, paths)
    # and for plain natural-language questions, where it is noisier (see docs/decisions.md).
    rrf_weight_keyword: float = 1.0
    rrf_weight_keyword_natural: float = 0.25
    rrf_weight_semantic: float = 1.0
    # The lexical feature reranker did not improve the benchmark, so it is opt-in per request.
    search_rerank_default: bool = False
    search_snippet_max_lines: int = 40
    search_snippet_max_chars: int = 2400

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
