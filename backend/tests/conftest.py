from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", frontend_origin="http://localhost:3000")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    """Settings for a temporary database migrated with the real Alembic revisions."""
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
    get_settings.cache_clear()
    return Settings(
        database_url=database_url,
        workspace_dir=str(tmp_path / "workspaces"),
        max_upload_bytes=1024 * 1024,
        max_download_bytes=2 * 1024 * 1024,
        # Never touch the development vector store or download model weights in tests.
        chroma_dir=str(tmp_path / "chroma"),
        embedding_provider="fake",
        embedding_model="fake-hash-64",
        embedding_cache_dir=str(tmp_path / "models"),
    )


@pytest.fixture
def api(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
