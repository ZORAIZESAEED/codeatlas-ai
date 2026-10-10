"""Embedding provider selection. Providers are cached per configuration; creating one never
loads a model (loading happens on the first embed call)."""
import threading

from app.core.config import Settings
from app.services.embeddings.base import EmbeddingError, EmbeddingProvider
from app.services.embeddings.fake_provider import FakeEmbeddingProvider
from app.services.embeddings.local_provider import FastEmbedProvider

PROVIDERS = ("fastembed", "fake")

_cache: dict[tuple, EmbeddingProvider] = {}
_lock = threading.Lock()


def get_provider(
    settings: Settings, provider: str | None = None, model: str | None = None
) -> EmbeddingProvider:
    """The configured provider, or the one a project was indexed with (``provider``/``model``)
    so queries are always embedded by the same model as the indexed chunks."""
    name = provider or settings.embedding_provider
    model_name = model or settings.embedding_model
    if name not in PROVIDERS:
        raise EmbeddingError(f"Unknown embedding provider '{name}'.")
    key = (
        name, model_name, settings.embedding_cache_dir, settings.embedding_threads,
        settings.embedding_batch_size, settings.embedding_query_prefix,
    )
    with _lock:
        cached = _cache.get(key)
        if cached is None:
            if name == "fake":
                # "fake-hash-<dimension>" selects the dimension (default 64).
                suffix = model_name.rsplit("-", 1)[-1]
                dimension = int(suffix) if suffix.isdigit() and 0 < int(suffix) <= 4096 else 64
                cached = FakeEmbeddingProvider(model_name, dimension)
            else:
                cached = FastEmbedProvider(
                    model_name,
                    cache_dir=settings.embedding_cache_dir,
                    threads=settings.embedding_threads,
                    batch_size=settings.embedding_batch_size,
                    query_prefix=settings.embedding_query_prefix,
                )
            _cache[key] = cached
        return cached
