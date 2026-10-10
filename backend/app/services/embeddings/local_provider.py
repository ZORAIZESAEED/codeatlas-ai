"""Local CPU embeddings with fastembed (ONNX Runtime). Source code never leaves the machine.

The model is loaded lazily on first use (indexing or a semantic query), never at import or
server start. The first load downloads the model files from Hugging Face into
``EMBEDDING_CACHE_DIR``; later loads are offline.
"""
import logging
import os
import threading

from app.services.embeddings.base import EmbeddingError, EmbeddingProvider

logger = logging.getLogger(__name__)

_BGE_QUERY = "Represent this sentence for searching relevant passages: "
# Documented query/document instructions of supported models (prefix match on the model id).
_QUERY_PREFIXES = {
    "BAAI/bge-small-en-v1.5": _BGE_QUERY,
    "BAAI/bge-base-en-v1.5": _BGE_QUERY,
    "snowflake/snowflake-arctic-embed": _BGE_QUERY,
    "nomic-ai/nomic-embed-text": "search_query: ",
}
_DOCUMENT_PREFIXES = {"nomic-ai/nomic-embed-text": "search_document: "}


def _lookup(table: dict[str, str], model: str) -> str:
    return next((v for k, v in table.items() if model.startswith(k)), "")


class FastEmbedProvider(EmbeddingProvider):
    name = "fastembed"

    def __init__(
        self,
        model: str,
        cache_dir: str | None = None,
        threads: int | None = None,
        batch_size: int = 32,
        query_prefix: str | None = None,
    ) -> None:
        self._model_name = model
        self._cache_dir = cache_dir
        self._threads = threads
        self._batch_size = max(1, batch_size)
        self._query_prefix = (
            query_prefix if query_prefix is not None else _lookup(_QUERY_PREFIXES, model)
        )
        self._document_prefix = _lookup(_DOCUMENT_PREFIXES, model)
        self._model = None
        self._dimension: int | None = None
        self._lock = threading.Lock()

    def model_identifier(self) -> str:
        return self._model_name

    def embedding_dimension(self) -> int:
        if self._dimension is None:
            self._dimension = self._catalog_dimension()
        return self._dimension

    def _catalog_dimension(self) -> int:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingError("fastembed is not installed (pip install fastembed).") from exc
        for entry in TextEmbedding.list_supported_models():
            if entry["model"].lower() == self._model_name.lower():
                return int(entry["dim"])
        raise EmbeddingError(
            f"Embedding model '{self._model_name}' is not supported by fastembed. "
            "See docs/decisions.md for supported choices."
        )

    def _load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
                # Windows without Developer Mode has no symlinks; the cache still works.
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                try:
                    from fastembed import TextEmbedding

                    self.embedding_dimension()
                    if self._cache_dir:
                        os.makedirs(self._cache_dir, exist_ok=True)
                    logger.info("Loading embedding model %s", self._model_name)
                    self._model = TextEmbedding(
                        model_name=self._model_name,
                        cache_dir=self._cache_dir,
                        threads=self._threads,
                    )
                except EmbeddingError:
                    raise
                except Exception as exc:
                    raise EmbeddingError(
                        f"Could not load embedding model '{self._model_name}' "
                        f"({type(exc).__name__}). The first use downloads it from Hugging Face; "
                        "check the network connection or EMBEDDING_CACHE_DIR."
                    ) from exc
        return self._model

    def _run(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        try:
            return [v.tolist() for v in model.embed(texts, batch_size=self._batch_size)]
        except Exception as exc:
            raise EmbeddingError(f"Embedding failed ({type(exc).__name__}).") from exc

    def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._document_prefix:
            texts = [self._document_prefix + t for t in texts]
        return self._run(texts)

    def _embed_query(self, text: str) -> list[float]:
        return self._run([self._query_prefix + text])[0]
