import threading

import pytest

from app.services.embeddings.fake_provider import FakeEmbeddingProvider
from app.services.indexing import vector_store
from app.services.indexing.vector_store import VectorCollection
from tests.search_helpers import (
    SEARCH_REPO,
    chroma_ids,
    db_rows,
    import_repo,
    index,
    search,
    workspace_file,
    write_lf,
)


@pytest.fixture
def embed_calls(monkeypatch):
    """Records every batch of texts sent to the (fake) embedding model."""
    calls: list[list[str]] = []
    original = FakeEmbeddingProvider._embed_documents

    def tracking(self, texts):
        calls.append(list(texts))
        return original(self, texts)

    monkeypatch.setattr(FakeEmbeddingProvider, "_embed_documents", tracking)
    return calls


def embedded(calls) -> int:
    return sum(len(c) for c in calls)


@pytest.fixture
def project(api):
    return import_repo(api)


def manifest_count(settings, project_id):
    return db_rows(settings, "SELECT count(*) FROM index_entries WHERE project_id = ?", project_id)[
        0
    ][0]


def fts_count(settings):
    return db_rows(settings, "SELECT count(*) FROM chunk_fts")[0][0]


def test_status_before_indexing(api, project):
    status = api.get(f"/api/projects/{project['id']}/index-status").json()
    assert status["status"] == "pending"
    assert status["indexed_chunk_count"] == 0
    assert status["configured_model"] == "fake-hash-64"
    assert status["analysis_chunk_count"] == project["chunk_count"] > 0


def test_index_inserts_all_chunks(api, project, settings, embed_calls):
    status = index(api, project["id"])
    assert status["status"] == "ready"
    assert status["embedding_provider"] == "fake"
    assert status["embedding_model"] == "fake-hash-64"
    assert status["embedding_dimension"] == 64
    assert status["input_version"] == "v1"
    total = project["chunk_count"]
    assert status["indexed_chunk_count"] == status["total_chunks"] == total
    assert status["processed_chunks"] == total
    assert status["embedded_count"] == total and status["reused_count"] == 0
    assert status["last_indexed_at"] and status["duration_ms"] is not None
    assert embedded(embed_calls) == total
    # Batched: never one call per chunk when several chunks are pending.
    assert len(embed_calls) < total
    assert manifest_count(settings, project["id"]) == total == fts_count(settings)
    assert len(chroma_ids(settings, project["id"])) == total


def test_chroma_records_carry_metadata(api, project, settings):
    index(api, project["id"])
    (name,) = db_rows(settings, "SELECT collection_name FROM search_indexes")[0]
    col = VectorCollection(settings, name, create=False)._col
    record = col.get(limit=1, include=["metadatas", "embeddings"])
    meta = record["metadatas"][0]
    for key in (
        "project_id", "path", "language", "symbol", "start_line", "end_line", "content_hash",
        "analysis_revision", "embedding_model", "input_version", "chunk_kind",
    ):
        assert key in meta
    assert meta["project_id"] == project["id"]
    assert len(record["embeddings"][0]) == 64
    # The same embeddings are not duplicated into SQLite.
    columns = {r[1] for r in db_rows(settings, "PRAGMA table_info(index_entries)")}
    assert not columns & {"embedding", "vector"}


def test_reindex_unchanged_is_idempotent(api, project, settings, embed_calls):
    first = index(api, project["id"])
    ids_before = chroma_ids(settings, project["id"])
    embed_calls.clear()
    second = index(api, project["id"])
    assert embedded(embed_calls) == 0
    assert second["embedded_count"] == 0 and second["removed_count"] == 0
    assert second["indexed_chunk_count"] == first["indexed_chunk_count"]
    assert chroma_ids(settings, project["id"]) == ids_before
    assert manifest_count(settings, project["id"]) == first["indexed_chunk_count"]
    assert fts_count(settings) == first["indexed_chunk_count"]


def test_reanalysis_marks_index_stale(api, project):
    index(api, project["id"])
    assert api.post(f"/api/projects/{project['id']}/analyze").status_code == 200
    status = api.get(f"/api/projects/{project['id']}/index-status").json()
    assert status["status"] == "stale"
    assert "re-analyzed" in status["stale_reasons"][0]
    # Reindexing identical chunks after re-analysis embeds nothing and is ready again.
    again = index(api, project["id"])
    assert again["status"] == "ready" and again["embedded_count"] == 0


def test_changed_and_removed_chunks_are_updated(api, project, settings, embed_calls):
    pid = project["id"]
    index(api, pid)
    old_ids = chroma_ids(settings, pid)
    # Edit one function, delete another file entirely, then re-analyze.
    auth = workspace_file(settings, pid, "app/auth.py")
    write_lf(auth, auth.read_text().replace('"invalid credentials"', '"wrong password supplied"'))
    workspace_file(settings, pid, "web/validate.js").unlink()
    api.post(f"/api/projects/{pid}/analyze")
    embed_calls.clear()

    status = index(api, pid)
    assert status["status"] == "ready"
    assert status["embedded_count"] == 1  # only the edited AuthService chunk
    assert embedded(embed_calls) == 1
    assert status["removed_count"] >= 2  # old AuthService chunk + validate.js chunk(s)
    new_ids = chroma_ids(settings, pid)
    assert len(new_ids) == status["indexed_chunk_count"] == manifest_count(settings, pid)
    assert len(old_ids - new_ids) == status["removed_count"]
    paths = {r[0] for r in db_rows(settings, "SELECT path FROM index_entries")}
    assert "web/validate.js" not in paths

    hits = search(api, pid, "wrong password supplied", mode="keyword")["results"]
    assert hits[0]["symbol_name"] == "AuthService"
    results = search(api, pid, "validateRequest", mode="keyword")["results"]
    assert all(r["file_path"] != "web/validate.js" for r in results)


def test_moved_chunk_reuses_vector(api, project, settings, embed_calls):
    pid = project["id"]
    index(api, pid)
    db = workspace_file(settings, pid, "app/database.py")
    write_lf(db, "# moved down by two lines\n\n" + db.read_text())
    api.post(f"/api/projects/{pid}/analyze")
    embed_calls.clear()
    status = index(api, pid)
    # Chunks of database.py have new line ranges (new record ids) but identical embedding
    # input except where the new comment lands in the first chunk.
    assert status["reused_count"] >= 1
    assert status["embedded_count"] == embedded(embed_calls) <= 1
    rows = db_rows(
        settings,
        "SELECT start_line FROM index_entries WHERE path = 'app/database.py' ORDER BY start_line",
    )
    assert rows[-1][0] > 4  # line ranges follow the new layout


def test_project_isolation(api, settings):
    first = import_repo(api)
    other = import_repo(
        api,
        {"o/private_logic.py": "def only_in_other_project():\n    return 'xyzzy'\n"},
        name="other",
    )
    index(api, first["id"])
    index(api, other["id"])
    names = {r[0] for r in db_rows(settings, "SELECT collection_name FROM search_indexes")}
    assert len(names) == 2  # one collection per project
    for mode in ("keyword", "semantic", "hybrid"):
        results = search(api, first["id"], "only_in_other_project xyzzy", mode=mode)["results"]
        assert all(r["file_path"] != "private_logic.py" for r in results)
        own = search(api, other["id"], "only_in_other_project", mode=mode)["results"]
        assert own and all(r["file_path"] == "private_logic.py" for r in own)


def test_model_change_marks_stale_and_rebuilds_separately(api, project, settings, embed_calls):
    pid = project["id"]
    first = index(api, pid)
    old_collection = db_rows(settings, "SELECT collection_name FROM search_indexes")[0][0]
    api.app.state.settings = settings.model_copy(update={"embedding_model": "fake-hash-32"})

    status = api.get(f"/api/projects/{pid}/index-status").json()
    assert status["status"] == "stale"
    assert "embedding model" in status["stale_reasons"][0]
    # Queries still use the indexed model (64 dimensions) until the project is reindexed.
    assert search(api, pid, "access token", mode="semantic")["results"]

    embed_calls.clear()
    rebuilt = index(api, pid)
    assert rebuilt["status"] == "ready"
    assert rebuilt["embedding_model"] == "fake-hash-32" and rebuilt["embedding_dimension"] == 32
    assert rebuilt["embedded_count"] == first["indexed_chunk_count"]  # vectors not mixed
    new_collection = db_rows(settings, "SELECT collection_name FROM search_indexes")[0][0]
    assert new_collection != old_collection
    assert not vector_store.collection_exists(settings, old_collection)
    assert search(api, pid, "access token", mode="semantic")["results"]


def test_failed_index_run_keeps_previous_index_and_recovers(
    api, project, settings, monkeypatch, embed_calls
):
    pid = project["id"]
    first = index(api, pid)
    api.post(f"/api/projects/{pid}/analyze")
    write_lf(
        workspace_file(settings, pid, "app/database.py"),
        "def init_db(url):\n    return connect_pool(url)\n",
    )
    api.post(f"/api/projects/{pid}/analyze")

    def broken_upsert(self, *args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(VectorCollection, "upsert", broken_upsert)
    failed = index(api, pid)
    assert failed["status"] == "failed"
    assert "RuntimeError" in failed["error"] and "previous index was kept" in failed["error"]
    # The previous manifest is intact and still searchable, flagged as such.
    assert manifest_count(settings, pid) == first["indexed_chunk_count"]
    response = search(api, pid, "init_db", mode="keyword")
    assert response["index_status"]["status"] == "failed"
    assert any("failed" in w for w in response["warnings"])

    monkeypatch.undo()
    recovered = index(api, pid)
    assert recovered["status"] == "ready" and recovered["error"] is None
    assert len(chroma_ids(settings, pid)) == manifest_count(settings, pid)
    assert recovered["indexed_chunk_count"] == manifest_count(settings, pid)


def test_failure_after_vectors_written_is_reconciled(api, project, settings, monkeypatch):
    """Vectors written to ChromaDB before a SQLite failure are reused, not duplicated."""
    pid = project["id"]
    from app.services.indexing import manifest

    def broken_add_fts(*args, **kwargs):
        raise RuntimeError("sqlite failure")

    monkeypatch.setattr(manifest, "add_fts", broken_add_fts)
    failed = index(api, pid)
    assert failed["status"] == "failed"
    assert manifest_count(settings, pid) == 0  # SQLite rolled back
    orphan_vectors = chroma_ids(settings, pid)
    assert orphan_vectors  # ChromaDB kept the upserts

    monkeypatch.undo()
    status = index(api, pid)
    assert status["status"] == "ready"
    assert status["embedded_count"] == 0  # content-addressed ids: vectors reused
    assert chroma_ids(settings, pid) == orphan_vectors
    assert manifest_count(settings, pid) == len(orphan_vectors)


def test_failed_vector_cleanup_is_ignored_by_search_and_fixed_next_run(
    api, project, settings, monkeypatch
):
    pid = project["id"]
    index(api, pid)
    workspace_file(settings, pid, "web/validate.js").unlink()
    api.post(f"/api/projects/{pid}/analyze")

    def broken_delete(self, ids):
        raise RuntimeError("locked")

    monkeypatch.setattr(VectorCollection, "delete", broken_delete)
    status = index(api, pid)
    assert status["status"] == "ready"  # SQLite is the source of truth
    assert len(chroma_ids(settings, pid)) > status["indexed_chunk_count"]
    results = search(api, pid, "validate request username required", mode="semantic")
    assert all(r["file_path"] != "web/validate.js" for r in results["results"])

    monkeypatch.undo()
    index(api, pid)
    assert len(chroma_ids(settings, pid)) == manifest_count(settings, pid)


def test_embedding_provider_error_marks_failed(api, project, monkeypatch):
    from app.services.embeddings.base import EmbeddingError

    def unavailable(self, texts):
        raise EmbeddingError("Could not load embedding model 'x'.")

    monkeypatch.setattr(FakeEmbeddingProvider, "_embed_documents", unavailable)
    status = index(api, project["id"])
    assert status["status"] == "failed"
    assert status["error"] == "Could not load embedding model 'x'."
    # Imported files are untouched by an indexing error.
    files = api.get(f"/api/projects/{project['id']}/files").json()["files"]
    assert len(files) == len(SEARCH_REPO)


def test_limits_fail_without_marking_ready(api, project, settings):
    api.app.state.settings = settings.model_copy(update={"index_max_chunks": 2})
    status = index(api, project["id"])
    assert status["status"] == "failed"
    assert "INDEX_MAX_CHUNKS" in status["error"]

    api.app.state.settings = settings.model_copy(
        update={"index_timeout_seconds": 0, "embedding_batch_size": 1}
    )
    status = index(api, project["id"])
    assert status["status"] == "failed"
    assert "INDEX_TIMEOUT_SECONDS" in status["error"]


def test_concurrent_indexing_is_rejected(api, project, monkeypatch):
    from app.services.indexing import indexer

    entered, release = threading.Event(), threading.Event()
    original = indexer._build

    def slow_build(*args, **kwargs):
        entered.set()
        # Hold the run until the assertions below are done (the `finally` always releases it).
        # A timeout here would let the run finish early under load and race the 409 check.
        release.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(indexer, "_build", slow_build)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(api.post(f"/api/projects/{project['id']}/index"))
    )
    worker.start()
    try:
        assert entered.wait(10)
        status = api.get(f"/api/projects/{project['id']}/index-status").json()
        assert status["status"] == "indexing"
        second = api.post(f"/api/projects/{project['id']}/index")
        assert second.status_code == 409
        busy = api.post(f"/api/projects/{project['id']}/search", json={"query": "token"})
        assert busy.status_code == 409  # nothing indexed yet
    finally:
        release.set()
        worker.join(120)
    assert not worker.is_alive(), "the first indexing request never completed"
    # The first request itself succeeded (not just some later state).
    assert results[0].status_code == 200 and results[0].json()["status"] == "ready"
    assert api.get(f"/api/projects/{project['id']}/index-status").json()["status"] == "ready"


def test_index_requires_analysis_and_project(api, project, settings):
    assert api.post("/api/projects/missing/index").status_code == 404
    assert api.get("/api/projects/missing/index-status").status_code == 404
    db_rows(settings, "UPDATE projects SET analysis_status = 'failed'")
    response = api.post(f"/api/projects/{project['id']}/index")
    assert response.status_code == 409
    assert "analysis" in response.json()["detail"]


def test_delete_project_removes_index_data(api, settings):
    keep = import_repo(api, {"k/keep.py": "def keep_me():\n    pass\n"}, name="keep")
    gone = import_repo(api)
    index(api, keep["id"])
    index(api, gone["id"])
    gone_collection = db_rows(
        settings, "SELECT collection_name FROM search_indexes WHERE project_id = ?", gone["id"]
    )[0][0]
    keep_ids = chroma_ids(settings, keep["id"])

    assert api.delete(f"/api/projects/{gone['id']}").status_code == 204
    for table in ("index_entries", "search_indexes"):
        sql = f"SELECT count(*) FROM {table} WHERE project_id = ?"
        assert db_rows(settings, sql, gone["id"])[0][0] == 0
    assert fts_count(settings) == manifest_count(settings, keep["id"])
    assert not vector_store.collection_exists(settings, gone_collection)
    # The other project is untouched.
    assert chroma_ids(settings, keep["id"]) == keep_ids
    assert search(api, keep["id"], "keep_me", mode="hybrid")["results"]


def test_orphan_collections_are_cleaned_on_next_index(api, settings, monkeypatch):
    gone = import_repo(api)
    index(api, gone["id"])
    name = db_rows(settings, "SELECT collection_name FROM search_indexes")[0][0]
    from app.services.indexing import indexer

    monkeypatch.setattr(indexer.vector_store, "delete_collections", _raise)
    api.delete(f"/api/projects/{gone['id']}")
    assert vector_store.collection_exists(settings, name)  # cleanup failed, logged
    monkeypatch.undo()

    other = import_repo(api, {"x/a.py": "def a():\n    pass\n"}, name="x")
    index(api, other["id"])
    assert not vector_store.collection_exists(settings, name)


def _raise(*args, **kwargs):
    raise RuntimeError("chroma unavailable")


def _restart(settings):
    """A new app instance on the same storage, as after a server restart."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(settings))


def test_process_death_mid_index_is_recovered_on_restart(api, project, settings, embed_calls,
                                                          monkeypatch):
    pid = project["id"]
    first = index(api, pid)
    write_lf(workspace_file(settings, pid, "app/database.py"), "def init_db(url):\n    pass\n")
    api.post(f"/api/projects/{pid}/analyze")
    from app.services.indexing import indexer, manifest

    class ProcessKilled(BaseException):
        """Not an Exception: skips the indexer's failure handler, like a killed process."""

    def die(*args, **kwargs):
        raise ProcessKilled

    monkeypatch.setattr(manifest, "delete_entries", die)  # after vectors, before the swap
    with api.app.state.session_factory() as session, pytest.raises(ProcessKilled):
        indexer.index_project(session, settings, pid)
    monkeypatch.undo()
    # Without recovery the row would stay "indexing" (vectors written, manifest not swapped).
    assert db_rows(settings, "SELECT status FROM search_indexes")[0][0] == "indexing"
    written_before_crash = embedded(embed_calls)
    assert written_before_crash >= 1

    with _restart(settings) as restarted:
        status = restarted.get(f"/api/projects/{pid}/index-status").json()
        assert status["status"] == "failed"
        assert "interrupted" in status["error"] and "still searchable" in status["error"]
        assert "re-analyzed" in status["stale_reasons"][0]  # the surviving index is stale too
        # The previous complete index is intact and searchable, flagged clearly.
        assert manifest_count(settings, pid) == first["indexed_chunk_count"]
        response = search(restarted, pid, "init_db", mode="hybrid")
        assert response["results"]
        assert any("failed" in w and "also stale" in w for w in response["warnings"])

        embed_calls.clear()
        recovered = index(restarted, pid)
        assert recovered["status"] == "ready" and recovered["error"] is None
        assert embedded(embed_calls) == 0  # vectors written before the crash were reused
        assert len(chroma_ids(settings, pid)) == manifest_count(settings, pid)
        assert fts_count(settings) == manifest_count(settings, pid)


def test_restart_recovery_ignores_finished_states(api, project, settings):
    index(api, project["id"])
    with _restart(settings) as restarted:
        assert restarted.get(f"/api/projects/{project['id']}/index-status").json()[
            "status"] == "ready"


def test_startup_without_search_tables_does_not_crash(client):
    # The `client` fixture's database has no tables (not migrated).
    assert client.get("/health").status_code == 200


def test_first_index_failure_is_explained_by_search(api, project, monkeypatch):
    def broken_upsert(self, *args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(VectorCollection, "upsert", broken_upsert)
    assert index(api, project["id"])["status"] == "failed"
    response = api.post(f"/api/projects/{project['id']}/search", json={"query": "token"})
    assert response.status_code == 409
    assert "Indexing failed and no earlier index exists" in response.json()["detail"]


def test_completed_empty_index_returns_no_results(api, settings):
    project = import_repo(api, {"e/a.py": "x = 1\n", "e/b.py": "y = 2\n"}, name="tiny")
    index(api, project["id"])
    # Empty every file, re-analyze and reindex: the completed index is legitimately empty.
    for path in ("a.py", "b.py"):
        write_lf(workspace_file(settings, project["id"], path), "")
    api.post(f"/api/projects/{project['id']}/analyze")
    status = index(api, project["id"])
    assert status["status"] == "ready" and status["indexed_chunk_count"] == 0
    response = search(api, project["id"], "anything", mode="hybrid")
    assert response["results"] == []
