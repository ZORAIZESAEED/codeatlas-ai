import math
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.embeddings.base import EmbeddingError, EmbeddingProvider
from app.services.embeddings.fake_provider import FakeEmbeddingProvider
from app.services.embeddings.local_provider import FastEmbedProvider
from app.services.embeddings.registry import get_provider
from app.services.indexing.documents import (
    EMBEDDING_INPUT_VERSION,
    ChunkRef,
    embed_hash,
    embedding_text,
    keyword_fields,
    record_id,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REF = ChunkRef("app/auth.py", 10, 20, "symbol", "AuthService.createToken", "method", "Python", "h")


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


def test_fake_provider_is_deterministic_and_normalized():
    provider = FakeEmbeddingProvider()
    first = provider.embed_documents(["create access token", "open database"])
    again = provider.embed_documents(["create access token", "open database"])
    assert first == again
    assert all(len(v) == 64 for v in first)
    assert all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-9 for v in first)
    assert provider.embed_query("create access token") == first[0]


def test_fake_provider_similarity_follows_shared_words():
    provider = FakeEmbeddingProvider()
    query = provider.embed_query("access token")
    token_doc, db_doc = provider.embed_documents(["createAccessToken()", "init_db(url)"])
    assert cosine(query, token_doc) > cosine(query, db_doc)


def test_batch_embedding_keeps_order_and_handles_empty():
    provider = FakeEmbeddingProvider()
    texts = [f"text number {i}" for i in range(50)]
    batch = provider.embed_documents(texts)
    assert batch == [provider.embed_documents([t])[0] for t in texts]
    assert provider.embed_documents([]) == []


class BrokenProvider(EmbeddingProvider):
    name = "broken"

    def __init__(self, vectors):
        self.vectors = vectors

    def model_identifier(self):
        return "broken"

    def embedding_dimension(self):
        return 3

    def _embed_documents(self, texts):
        return self.vectors

    def _embed_query(self, text):
        return self.vectors[0]


@pytest.mark.parametrize(
    "vectors, message",
    [
        ([[1.0, 0.0]], "dimension"),
        ([[1.0, 0.0, float("nan")]], "non-finite"),
        ([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], "returned 2 vectors"),
    ],
)
def test_provider_output_is_validated(vectors, message):
    with pytest.raises(EmbeddingError, match=message):
        BrokenProvider(vectors).embed_documents(["only one"])


def test_empty_query_is_rejected():
    with pytest.raises(EmbeddingError):
        FakeEmbeddingProvider().embed_query("   ")


def test_registry_uses_configuration_and_caches(tmp_path):
    settings = Settings(embedding_provider="fake", embedding_model="fake-hash-32")
    provider = get_provider(settings)
    assert provider.name == "fake"
    assert provider.model_identifier() == "fake-hash-32"
    assert provider.embedding_dimension() == 32
    assert get_provider(settings) is provider
    # An index's recorded model wins over the configured one.
    assert get_provider(settings, "fake", "fake-hash-16").embedding_dimension() == 16
    with pytest.raises(EmbeddingError, match="Unknown embedding provider"):
        get_provider(settings, "remote-api")


def test_local_provider_is_lazy_and_configurable(tmp_path):
    settings = Settings(
        embedding_provider="fastembed",
        embedding_model="BAAI/bge-small-en-v1.5",
        embedding_cache_dir=str(tmp_path / "models"),
    )
    provider = get_provider(settings)
    assert isinstance(provider, FastEmbedProvider)
    assert provider.model_identifier() == "BAAI/bge-small-en-v1.5"
    # Dimension comes from the fastembed catalog without loading (or downloading) the model.
    assert provider.embedding_dimension() == 384
    assert provider._model is None
    assert not (tmp_path / "models").exists()
    assert provider._query_prefix.startswith("Represent this sentence")


def test_local_provider_rejects_unknown_model_without_download(tmp_path):
    provider = FastEmbedProvider("not-a/real-model", cache_dir=str(tmp_path / "m"))
    with pytest.raises(EmbeddingError, match="not supported"):
        provider.embed_documents(["x"])
    assert not (tmp_path / "m").exists()


def test_query_prefix_override():
    assert FastEmbedProvider("BAAI/bge-small-en-v1.5", query_prefix="")._query_prefix == ""
    assert FastEmbedProvider("sentence-transformers/all-MiniLM-L6-v2")._query_prefix == ""


def test_app_startup_does_not_load_embedding_or_vector_libraries(tmp_path):
    # Fresh interpreter: importing and starting the app must not import (or download) models.
    code = (
        "import sys\n"
        "from fastapi.testclient import TestClient\n"
        "from app.core.config import Settings\n"
        "from app.main import create_app\n"
        f"s = Settings(database_url='sqlite:///{(tmp_path / 'x.db').as_posix()}')\n"
        "with TestClient(create_app(s)) as c:\n"
        "    assert c.get('/health').status_code == 200\n"
        "print(sorted(m for m in ('fastembed', 'chromadb', 'onnxruntime') if m in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=BACKEND_DIR, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_embedding_text_keeps_header_and_respects_limit():
    code = "def createToken():\n" + "    x = 1\n" * 1000
    text = embedding_text(REF, code, max_chars=500)
    assert text.startswith("File: app/auth.py\nLanguage: Python\n")
    assert "Symbol: AuthService.createToken (method)" in text
    assert "Name words: auth service create token" in text
    assert len(text) <= 500
    assert "10" not in text.split("Code:")[0]  # no line numbers: moved chunks keep their input


def test_record_ids_are_deterministic_and_content_addressed():
    assert record_id("p1", REF) == record_id("p1", REF)
    assert record_id("p1", REF) != record_id("p2", REF)
    moved = ChunkRef(**{**REF.__dict__, "start_line": 11, "end_line": 21})
    changed = ChunkRef(**{**REF.__dict__, "content_hash": "other"})
    assert len({record_id("p1", REF), record_id("p1", moved), record_id("p1", changed)}) == 3
    assert embed_hash("abc") == embed_hash("abc") != embed_hash("abd")
    assert EMBEDDING_INPUT_VERSION


def test_keyword_fields_add_identifier_parts():
    path, symbol, body = keyword_fields(REF, "return createAccessToken(user_id)\n")
    assert "app/auth.py" in path and "auth" in path
    assert symbol.startswith("AuthService.createToken") and "service" in symbol
    for word in ("create", "access", "token", "user", "createAccessToken", "user_id"):
        assert word in body
