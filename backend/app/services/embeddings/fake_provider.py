"""Deterministic embedding provider for tests: no model download, no randomness.

Uses the hashing trick over lower-cased word tokens (and split identifier parts), so texts that
share words get similar vectors. It is a lexical stand-in, not a semantic model.
"""
import hashlib
import math
import re

from app.services.embeddings.base import EmbeddingProvider

_WORD = re.compile(r"[A-Za-z0-9]+")


def _tokens(text: str) -> list[str]:
    words = []
    for raw in _WORD.findall(text):
        for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", raw) or [raw]:
            words.append(part.lower())
    return words


class FakeEmbeddingProvider(EmbeddingProvider):
    name = "fake"

    def __init__(self, model: str = "fake-hash-64", dimension: int = 64) -> None:
        self._model = model
        self._dimension = dimension

    def model_identifier(self) -> str:
        return self._model

    def embedding_dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "little") % self._dimension
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0:
            vector[0], norm = 1.0, 1.0
        return [x / norm for x in vector]

    def _embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _embed_query(self, text: str) -> list[float]:
        return self._vector(text)
