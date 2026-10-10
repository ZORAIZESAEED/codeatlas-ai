"""End-to-end retrieval: import -> analyze -> index -> search -> open source -> change ->
re-analyze -> reindex -> delete. Uses the deterministic fake embedding provider."""
import hashlib
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.indexing import vector_store
from tests.search_helpers import (
    chroma_ids,
    db_rows,
    import_repo,
    index,
    search,
    workspace_file,
    write_lf,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_full_retrieval_lifecycle(api, settings):
    project = import_repo(api)
    pid = project["id"]
    assert project["analysis_status"] == "ready"

    status = index(api, pid)
    assert status["status"] == "ready" and status["indexed_chunk_count"] == project["chunk_count"]

    response = search(api, pid, "Where is the JWT access token created?", mode="hybrid")
    assert response["mode_used"] == "hybrid"
    assert response["index_status"]["status"] == "ready"
    hit = next(r for r in response["results"] if r["symbol_name"] == "create_access_token")

    # "Open" the citation exactly as the frontend does: fetch the file, slice the line range.
    source = api.get(f"/api/projects/{pid}/files/content", params={"path": hit["file_path"]})
    assert source.status_code == 200
    lines = source.json()["content"].split("\n")
    cited = "\n".join(lines[hit["start_line"] - 1 : hit["end_line"]])
    assert cited.startswith("def create_access_token(user_id, secret):")
    assert hit["snippet"].startswith(cited[:80])

    # Change the code, re-analyze (index becomes stale), reindex incrementally.
    auth = workspace_file(settings, pid, "app/auth.py")
    write_lf(
        auth,
        auth.read_text()
        + "\n\ndef revoke_refresh_token(token_store, token):\n    token_store.discard(token)\n",
    )
    api.post(f"/api/projects/{pid}/analyze")
    assert api.get(f"/api/projects/{pid}/index-status").json()["status"] == "stale"
    updated = index(api, pid)
    assert updated["status"] == "ready"
    assert updated["embedded_count"] == 1
    new_hit = search(api, pid, "revoke_refresh_token", mode="hybrid")["results"][0]
    assert new_hit["symbol_name"] == "revoke_refresh_token"
    assert new_hit["start_line"] > hit["end_line"]

    # Delete: SQLite rows, FTS rows and the vector collection are gone.
    collection = db_rows(settings, "SELECT collection_name FROM search_indexes")[0][0]
    assert chroma_ids(settings, pid)
    assert api.delete(f"/api/projects/{pid}").status_code == 204
    assert db_rows(settings, "SELECT count(*) FROM index_entries")[0][0] == 0
    assert db_rows(settings, "SELECT count(*) FROM chunk_fts")[0][0] == 0
    assert not vector_store.collection_exists(settings, collection)
    assert api.post(f"/api/projects/{pid}/search", json={"query": "token"}).status_code == 404


def test_regular_fts_table_on_older_sqlite(tmp_path, monkeypatch):
    """SQLite < 3.43 (no contentless_delete) gets a regular FTS5 table that works the same."""
    database_url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 40, 1))
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")
    monkeypatch.undo()
    get_settings.cache_clear()
    (ddl,) = db_rows(
        Settings(database_url=database_url),
        "SELECT sql FROM sqlite_master WHERE name = 'chunk_fts'",
    )[0]
    assert "contentless_delete" not in ddl

    settings = Settings(
        database_url=database_url, workspace_dir=str(tmp_path / "ws"),
        chroma_dir=str(tmp_path / "chroma"), embedding_provider="fake",
        embedding_model="fake-hash-64",
    )
    with TestClient(create_app(settings)) as api:
        project = import_repo(api)
        index(api, project["id"])
        assert search(api, project["id"], "create_access_token", mode="keyword")["results"]
        # Removal path: DELETE on the regular FTS table.
        workspace_file(settings, project["id"], "web/validate.js").unlink()
        api.post(f"/api/projects/{project['id']}/analyze")
        assert index(api, project["id"])["removed_count"] >= 1
        assert api.delete(f"/api/projects/{project['id']}").status_code == 204
        assert db_rows(settings, "SELECT count(*) FROM chunk_fts")[0][0] == 0


def test_migration_from_0003_keeps_data_and_indexes(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'm3.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "0003_code_analysis")

    project_id = "22222222-2222-4222-8222-222222222222"
    source = "def legacy_token():\n    return 1\n"
    workspace = tmp_path / "workspaces"
    (workspace / project_id).mkdir(parents=True)
    (workspace / project_id / "a.py").write_bytes(source.encode())
    db = sqlite3.connect(tmp_path / "m3.db")
    db.execute(
        "INSERT INTO projects (id, name, source_type, status, created_at, file_count,"
        " total_lines, total_bytes, skipped_count, analysis_status, symbol_count, chunk_count)"
        " VALUES (?, 'legacy', 'zip', 'ready', '2026-01-01', 1, 2, 31, 0, 'ready', 1, 1)",
        (project_id,),
    )
    db.execute(
        "INSERT INTO project_files (project_id, path, language, size_bytes, line_count,"
        " content_hash, parser) VALUES (?, 'a.py', 'Python', 31, 2, 'abc', 'python-ast')",
        (project_id,),
    )
    db.execute(
        "INSERT INTO code_chunks (project_id, path, chunk_index, start_line, end_line, kind,"
        " symbol, language, char_count, content_hash)"
        " VALUES (?, 'a.py', 0, 1, 2, 'symbol', 'legacy_token', 'Python', 31, ?)",
        (project_id, hashlib.sha256(source.encode()).hexdigest()),
    )
    db.commit()
    db.close()

    command.upgrade(config, "head")
    get_settings.cache_clear()
    settings = Settings(
        database_url=database_url, workspace_dir=str(workspace),
        chroma_dir=str(tmp_path / "chroma"), embedding_provider="fake",
        embedding_model="fake-hash-64",
    )
    with TestClient(create_app(settings)) as api:
        project = api.get(f"/api/projects/{project_id}").json()
        assert project["chunk_count"] == 1 and project["analysis_status"] == "ready"
        status = api.get(f"/api/projects/{project_id}/index-status").json()
        assert status["status"] == "pending" and status["analysis_revision"] == 0
        # Existing chunks are indexed without re-analysis.
        assert index(api, project_id)["indexed_chunk_count"] == 1
        result = search(api, project_id, "legacy_token")["results"][0]
        assert (result["file_path"], result["start_line"], result["end_line"]) == ("a.py", 1, 2)
