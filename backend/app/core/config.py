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

    # Chat models (Milestone 4). Contacted only when a chat request needs them, never at startup.
    # "ollama" (local) | "fake" (tests only). No model is assumed: set LLM_MODEL explicitly.
    llm_provider: str = "ollama"
    llm_model: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    llm_temperature: float = 0.1
    llm_max_output_tokens: int = 2048
    # Wall-clock limit for one model request (CPU-only machines generate a few tokens/s).
    llm_request_timeout_seconds: float = 300.0
    # Prompt + output budget. Sent to Ollama as num_ctx so it never truncates silently.
    llm_max_context_tokens: int = 8192
    # Optional pricing (USD per 1M tokens) to estimate cost from reported usage. Unset = no cost.
    llm_input_cost_per_mtok: float | None = None
    llm_output_cost_per_mtok: float | None = None

    # Retrieval-augmented answers.
    rag_search_mode: str = "hybrid"
    rag_search_limit: int = 8
    rag_max_evidence: int = 8
    # Conversation memory sent to the model: recent completed turns, bounded by tokens.
    chat_history_turns: int = 4
    chat_history_max_tokens: int = 1200
    chat_question_max_chars: int = 2000

    # Multi-step investigation limits.
    investigation_planner: str = "auto"  # auto (native tool calls if verified) | deterministic
    investigation_max_steps: int = 4
    investigation_max_tool_calls: int = 10
    investigation_max_lines_read: int = 800
    investigation_max_evidence: int = 12
    investigation_max_llm_requests: int = 5
    investigation_timeout_seconds: float = 900.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
