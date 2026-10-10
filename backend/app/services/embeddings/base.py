"""Embedding provider interface.

Providers turn text into fixed-size vectors. Document and query embedding are separate
because retrieval models often treat queries differently (e.g. an instruction prefix).
"""
import math
from abc import ABC, abstractmethod


class EmbeddingError(RuntimeError):
    """The provider could not be loaded or failed to embed. Messages never contain source."""


class EmbeddingProvider(ABC):
    #: Short provider name stored with every index ("fastembed", "fake").
    name: str = ""

    @abstractmethod
    def model_identifier(self) -> str: ...

    @abstractmethod
    def embedding_dimension(self) -> int: ...

    @abstractmethod
    def _embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def _embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._embed_documents(texts)
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Embedding provider returned {len(vectors)} vectors for {len(texts)} inputs."
            )
        return [self._validate(v) for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise EmbeddingError("Cannot embed an empty query.")
        return self._validate(self._embed_query(text))

    def _validate(self, vector) -> list[float]:
        values = [float(x) for x in vector]
        if len(values) != self.embedding_dimension():
            raise EmbeddingError(
                f"Embedding dimension {len(values)} does not match the expected "
                f"{self.embedding_dimension()} for {self.model_identifier()}."
            )
        if not all(math.isfinite(x) for x in values):
            raise EmbeddingError("Embedding contains non-finite values.")
        return values
