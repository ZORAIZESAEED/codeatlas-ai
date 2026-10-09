import hashlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from tests.conftest import BACKEND_DIR
from tests.helpers import make_zip

REPO = {
    "demo/app/service.py": (
        '"""Service module."""\n\n\nclass Service:\n    """Does work."""\n\n'
        "    def run(self):\n        return 1\n\n\ndef helper():\n    pass\n"
    ),
    "demo/web/App.tsx": (
        "export interface Props { title: string }\n\n"
        "export default function App(props: Props) {\n  return <h1>{props.title}</h1>;\n}\n"
    ),
    "demo/web/util.js": "export const add = (a, b) => a + b;\n",
    "demo/broken.py": "def broken(:\n    pass\n",
    "demo/README.md": "# Demo\n\nHello.\n\n## Usage\n\nRun it.\n",
    "demo/main.go": "package main\n\nfunc main() {}\n",
}


@pytest.fixture
def project(api):
    response = api.post(
        "/api/projects/import/zip", files={"file": ("demo.zip", make_zip(REPO), "application/zip")}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_import_runs_analysis(project):
    assert project["analysis_status"] == "ready"
    assert project["analyzed_at"]
    assert project["symbol_count"] == 6  # Service, Service.run, helper, Props, App, add
    assert project["chunk_count"] > 0


def test_list_symbols_and_filters(api, project):
    base = f"/api/projects/{project['id']}/symbols"
    body = api.get(base).json()
    assert body["total"] == 6
    names = {(s["path"], s["qualified_name"], s["kind"]) for s in body["symbols"]}
    assert names == {
        ("app/service.py", "Service", "class"),
        ("app/service.py", "Service.run", "method"),
        ("app/service.py", "helper", "function"),
        ("web/App.tsx", "Props", "interface"),
        ("web/App.tsx", "App", "function"),
        ("web/util.js", "add", "function"),
    }
    service = api.get(base, params={"path": "app/service.py", "kind": "class"}).json()
    assert [s["name"] for s in service["symbols"]] == ["Service"]
    assert service["symbols"][0]["docstring"] == "Does work."
    assert service["symbols"][0]["start_line"] == 4
    found = api.get(base, params={"q": "RUN"}).json()["symbols"]
    assert [s["qualified_name"] for s in found] == ["Service.run"]
    page = api.get(base, params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 6 and len(page["symbols"]) == 2


def test_chunks_with_content_match_source(api, project):
    path = "app/service.py"
    body = api.get(
        f"/api/projects/{project['id']}/chunks", params={"path": path, "include_content": True}
    ).json()
    source = api.get(
        f"/api/projects/{project['id']}/files/content", params={"path": path}
    ).json()["content"]
    lines = source.splitlines(keepends=True)
    assert [(c["kind"], c["symbol"]) for c in body["chunks"]] == [
        ("module", None), ("symbol", "Service"), ("symbol", "helper")
    ]
    for chunk in body["chunks"]:
        assert chunk["content"] == "".join(lines[chunk["start_line"] - 1 : chunk["end_line"]])
        assert chunk["char_count"] == len(chunk["content"])
        assert chunk["language"] == "Python"
    assert [c["chunk_index"] for c in body["chunks"]] == [0, 1, 2]


def test_chunks_for_fallback_files(api, project):
    base = f"/api/projects/{project['id']}/chunks"
    readme = api.get(base, params={"path": "README.md"}).json()["chunks"]
    # Small heading sections are merged into one chunk.
    assert [(c["kind"], c["start_line"], c["end_line"]) for c in readme] == [("text", 1, 7)]
    go = api.get(base, params={"path": "main.go"}).json()["chunks"]
    assert [(c["kind"], c["start_line"], c["end_line"]) for c in go] == [("text", 1, 3)]


def test_include_content_requires_path(api, project):
    response = api.get(f"/api/projects/{project['id']}/chunks", params={"include_content": True})
    assert response.status_code == 422


def test_files_report_parser_and_errors(api, project):
    files = {f["path"]: f for f in api.get(f"/api/projects/{project['id']}/files").json()["files"]}
    assert files["app/service.py"]["parser"] == "python-ast"
    assert files["web/App.tsx"]["parser"] == "tree-sitter-tsx"
    assert files["web/util.js"]["parser"] == "tree-sitter-javascript"
    assert files["main.go"]["parser"] == "line-fallback"
    assert files["broken.py"]["parser"] == "line-fallback"
    assert files["broken.py"]["parse_error"].startswith("Syntax error")


def test_overview_includes_analysis(api, project):
    analysis = api.get(f"/api/projects/{project['id']}/overview").json()["analysis"]
    assert analysis["status"] == "ready"
    assert analysis["symbols_by_kind"] == {"class": 1, "function": 3, "interface": 1, "method": 1}
    assert analysis["files_by_parser"] == {
        "line-fallback": 3, "python-ast": 1, "tree-sitter-javascript": 1, "tree-sitter-tsx": 1
    }
    assert analysis["issue_count"] == 1
    assert analysis["issues"][0]["path"] == "broken.py"


def test_reanalysis_is_idempotent(api, project):
    first = api.get(f"/api/projects/{project['id']}/chunks").json()
    response = api.post(f"/api/projects/{project['id']}/analyze")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol_count"] == project["symbol_count"]
    assert body["chunk_count"] == project["chunk_count"]
    second = api.get(f"/api/projects/{project['id']}/chunks").json()
    assert first == second


def test_delete_removes_analysis_only_for_project(api, project, settings):
    other = api.post(
        "/api/projects/import/zip",
        files={"file": ("o.zip", make_zip({"x.py": "def x():\n    pass\n"}), "application/zip")},
    ).json()
    assert api.delete(f"/api/projects/{project['id']}").status_code == 204
    db = sqlite3.connect(settings.database_url.removeprefix("sqlite:///"))
    for table in ("code_symbols", "code_chunks"):
        ids = {row[0] for row in db.execute(f"SELECT DISTINCT project_id FROM {table}")}
        assert ids == {other["id"]}
    db.close()


def test_analysis_endpoints_404_for_missing_project(api):
    missing = "00000000-0000-0000-0000-000000000000"
    assert api.post(f"/api/projects/{missing}/analyze").status_code == 404
    assert api.get(f"/api/projects/{missing}/symbols").status_code == 404
    assert api.get(f"/api/projects/{missing}/chunks").status_code == 404


def db_rows(settings, sql, *params):
    db = sqlite3.connect(settings.database_url.removeprefix("sqlite:///"))
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


def snapshot(api, project_id):
    files = api.get(f"/api/projects/{project_id}/files").json()["files"]
    return {
        "symbols": api.get(f"/api/projects/{project_id}/symbols", params={"limit": 1000}).json(),
        "chunks": api.get(f"/api/projects/{project_id}/chunks", params={"limit": 1000}).json(),
        "files": [(f["path"], f["content_hash"], f["parser"], f["parse_error"]) for f in files],
    }


def test_one_failing_file_does_not_fail_the_project(api, project, monkeypatch):
    import app.services.analysis as analysis

    real_chunk_source = analysis.chunk_source

    def flaky(text, language, symbols, *args, **kwargs):
        if "class Service" in text and symbols:  # structured pass of one file only
            raise RuntimeError("unexpected bug")
        return real_chunk_source(text, language, symbols, *args, **kwargs)

    monkeypatch.setattr(analysis, "chunk_source", flaky)
    body = api.post(f"/api/projects/{project['id']}/analyze").json()
    assert body["analysis_status"] == "ready"
    files = {f["path"]: f for f in api.get(f"/api/projects/{project['id']}/files").json()["files"]}
    assert files["app/service.py"]["parser"] == "line-fallback"
    assert "Analysis failed for this file (RuntimeError)" in files["app/service.py"]["parse_error"]
    chunks = api.get(
        f"/api/projects/{project['id']}/chunks", params={"path": "app/service.py"}
    ).json()["chunks"]
    assert [c["kind"] for c in chunks] == ["text"]  # still chunked, by lines
    assert files["web/App.tsx"]["parser"] == "tree-sitter-tsx"  # other files unaffected


def test_interrupted_analysis_keeps_previous_results(api, project, monkeypatch, settings):
    """A crash after old rows were deleted (just before commit) must roll everything back."""
    import app.services.analysis as analysis

    before = snapshot(api, project["id"])

    def crash():
        raise RuntimeError("process interrupted")

    monkeypatch.setattr(analysis, "_now", crash)
    body = api.post(f"/api/projects/{project['id']}/analyze").json()
    assert body["analysis_status"] == "failed"
    assert body["status"] == "ready"  # the import itself is untouched
    assert snapshot(api, project["id"]) == before
    assert body["symbol_count"] == project["symbol_count"]
    # The workspace files were not modified either.
    content = api.get(
        f"/api/projects/{project['id']}/files/content", params={"path": "app/service.py"}
    ).json()["content"]
    assert content == REPO["demo/app/service.py"]


def test_import_succeeds_when_analysis_crashes(api, monkeypatch):
    import app.services.analysis as analysis

    def crash():
        raise RuntimeError("analysis infrastructure failure")

    monkeypatch.setattr(analysis, "_now", crash)
    response = api.post(
        "/api/projects/import/zip", files={"file": ("demo.zip", make_zip(REPO), "application/zip")}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready" and body["analysis_status"] == "failed"
    assert body["file_count"] == 6 and body["symbol_count"] == 0
    assert api.get(f"/api/projects/{body['id']}/files").status_code == 200
    # Retrying once the problem is gone succeeds.
    monkeypatch.undo()
    retry = api.post(f"/api/projects/{body['id']}/analyze").json()
    assert retry["analysis_status"] == "ready" and retry["symbol_count"] == 6


def test_parse_errors_never_mark_import_failed(api):
    broken = {f"src/bad{i}.py": "def broken(:\n" for i in range(3)}
    broken["src/bad.ts"] = "function ( {\n"
    response = api.post(
        "/api/projects/import/zip", files={"file": ("b.zip", make_zip(broken), "application/zip")}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready" and body["analysis_status"] == "ready"
    assert body["chunk_count"] == 4
    issues = api.get(f"/api/projects/{body['id']}/overview").json()["analysis"]["issues"]
    assert len(issues) == 4


def test_project_deleted_during_analysis_leaves_no_orphans(api, project, monkeypatch, settings):
    import app.services.analysis as analysis
    from app.db.models import Project, ProjectFile

    real_chunk_source = analysis.chunk_source
    deleted = []

    def delete_mid_run(*args, **kwargs):
        if not deleted:
            with api.app.state.session_factory() as other:
                analysis.delete_analysis(other, project["id"])
                other.query(ProjectFile).filter_by(project_id=project["id"]).delete()
                other.query(Project).filter_by(id=project["id"]).delete()
                other.commit()
            deleted.append(True)
        return real_chunk_source(*args, **kwargs)

    monkeypatch.setattr(analysis, "chunk_source", delete_mid_run)
    response = api.post(f"/api/projects/{project['id']}/analyze")
    assert response.status_code == 404
    for table in ("code_symbols", "code_chunks", "project_files", "projects"):
        column = "id" if table == "projects" else "project_id"
        assert db_rows(settings, f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",
                       project["id"]) == [(0,)]


def test_chunk_references_are_valid_and_stable_across_reanalysis(api, project, settings):
    pid = project["id"]
    files = {f["path"]: f for f in api.get(f"/api/projects/{pid}/files").json()["files"]}
    runs = [snapshot(api, pid)]
    for _ in range(2):
        assert api.post(f"/api/projects/{pid}/analyze").status_code == 200
        runs.append(snapshot(api, pid))
    assert runs[0] == runs[1] == runs[2]  # deterministic ordering, ranges and hashes

    chunks = runs[-1]["chunks"]["chunks"]
    assert len({(c["path"], c["chunk_index"]) for c in chunks}) == len(chunks)
    for path in {c["path"] for c in chunks}:
        assert path in files  # every chunk points at a manifest file
        body = api.get(
            f"/api/projects/{pid}/chunks", params={"path": path, "include_content": True}
        ).json()
        source = api.get(f"/api/projects/{pid}/files/content", params={"path": path}).json()
        lines = [line + "\n" for line in source["content"].split("\n")][:files[path]["line_count"]]
        for chunk in body["chunks"]:
            assert 1 <= chunk["start_line"] <= chunk["end_line"] <= files[path]["line_count"]
            assert chunk["content"].rstrip("\n") == "".join(
                lines[chunk["start_line"] - 1 : chunk["end_line"]]
            ).rstrip("\n")
            assert chunk["content_hash"] == hashlib.sha256(chunk["content"].encode()).hexdigest()
    for symbol in runs[-1]["symbols"]["symbols"]:
        assert symbol["path"] in files
        line_count = files[symbol["path"]]["line_count"]
        assert 1 <= symbol["start_line"] <= symbol["end_line"] <= line_count

    # No duplicated rows in the database after three analyses.
    assert db_rows(settings, "SELECT COUNT(*) FROM code_chunks WHERE project_id = ?", pid) == [
        (project["chunk_count"],)
    ]
    assert db_rows(settings, "SELECT COUNT(*) FROM code_symbols WHERE project_id = ?", pid) == [
        (project["symbol_count"],)
    ]


def test_structural_parsing_size_limits(settings):
    limited = settings.model_copy(
        update={"parse_max_file_bytes": 200, "parse_max_total_bytes": 300}
    )
    files = {
        "a.py": "def a():\n    pass\n",  # 18 bytes: parsed
        "big.py": "def big():\n" + "    x = 1\n" * 30,  # > 200 bytes: line chunking
        "c.py": "def c():\n" + "    y = 2\n" * 15,  # 159 bytes: parsed (budget 300 - 18)
        "d.py": "def d():\n" + "    z = 3\n" * 15,  # 159 bytes: over the remaining budget
    }
    with TestClient(create_app(limited)) as client:
        body = client.post(
            "/api/projects/import/zip",
            files={"file": ("l.zip", make_zip(files), "application/zip")},
        ).json()
        assert body["status"] == "ready" and body["analysis_status"] == "ready"
        result = {
            f["path"]: (f["parser"], f["parse_error"])
            for f in client.get(f"/api/projects/{body['id']}/files").json()["files"]
        }
    assert result["a.py"] == ("python-ast", None)
    assert result["c.py"] == ("python-ast", None)
    assert result["big.py"][0] == "line-fallback"
    assert "structural parsing limit" in result["big.py"][1]
    assert result["d.py"][0] == "line-fallback"
    assert "parsing budget" in result["d.py"][1]
    assert body["chunk_count"] == 4  # every file is still chunked


def test_form_feed_file_end_to_end(api):
    source = "import os\n\x0c\ndef a():\n    return 1\n"
    body = api.post(
        "/api/projects/import/zip",
        files={"file": ("f.zip", make_zip({"ff.py": source}), "application/zip")},
    ).json()
    chunks = api.get(
        f"/api/projects/{body['id']}/chunks", params={"path": "ff.py", "include_content": True}
    ).json()["chunks"]
    assert [(c["symbol"], c["start_line"], c["end_line"], c["content"]) for c in chunks] == [
        (None, 1, 1, "import os\n"),
        ("a", 3, 4, "def a():\n    return 1\n"),
    ]


def test_existing_project_survives_migration_and_can_be_analyzed(tmp_path, monkeypatch):
    """A project imported under revision 0002 stays intact after upgrading to head."""
    database_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "0002_project_files")

    project_id = "11111111-1111-4111-8111-111111111111"
    workspace = tmp_path / "workspaces"
    (workspace / project_id).mkdir(parents=True)
    (workspace / project_id / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    db = sqlite3.connect(tmp_path / "legacy.db")
    db.execute(
        "INSERT INTO projects (id, name, source_type, status, created_at, file_count,"
        " total_lines, total_bytes, skipped_count)"
        " VALUES (?, 'legacy', 'zip', 'ready', '2026-01-01', 1, 2, 23, 0)",
        (project_id,),
    )
    db.execute(
        "INSERT INTO project_files (project_id, path, language, size_bytes, line_count,"
        " content_hash) VALUES (?, 'a.py', 'Python', 23, 2, 'abc')",
        (project_id,),
    )
    db.commit()
    db.close()

    command.upgrade(config, "head")
    get_settings.cache_clear()

    settings = Settings(database_url=database_url, workspace_dir=str(workspace))
    with TestClient(create_app(settings)) as api:
        before = api.get(f"/api/projects/{project_id}").json()
        assert before["name"] == "legacy" and before["file_count"] == 1
        assert before["analysis_status"] == "pending" and before["symbol_count"] == 0
        files = api.get(f"/api/projects/{project_id}/files").json()["files"]
        assert files[0]["path"] == "a.py" and files[0]["parser"] is None

        after = api.post(f"/api/projects/{project_id}/analyze").json()
        assert after["analysis_status"] == "ready" and after["symbol_count"] == 1
        assert Path(workspace / project_id / "a.py").read_text() == "def a():\n    return 1\n"
