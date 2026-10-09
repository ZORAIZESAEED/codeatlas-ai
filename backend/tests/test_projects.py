from pathlib import Path

import httpx
import pytest

from app.api.projects import get_github_client
from tests.helpers import SAMPLE_REPO, make_zip


def upload(api, data: bytes, filename: str = "sample.zip", **fields):
    return api.post(
        "/api/projects/import/zip",
        files={"file": (filename, data, "application/zip")},
        data=fields,
    )


@pytest.fixture
def project(api):
    response = upload(api, make_zip(SAMPLE_REPO), name="Sample Repo")
    assert response.status_code == 201, response.text
    return response.json()


def workspace_entries(settings) -> list[Path]:
    root = Path(settings.workspace_dir)
    return list(root.iterdir()) if root.exists() else []


def test_zip_import_creates_project(api, project, settings):
    assert project["name"] == "Sample Repo"
    assert project["source_type"] == "zip"
    assert project["status"] == "ready"
    assert project["file_count"] == 6
    assert project["skipped_count"] == 5
    assert project["total_lines"] == 10
    assert set(project["languages"]) == {"Python", "Markdown", "TypeScript", "TSX", "YAML"}
    assert (Path(settings.workspace_dir) / project["id"] / "app" / "main.py").is_file()


def test_zip_name_defaults_to_filename(api):
    response = upload(api, make_zip({"a.py": "x = 1\n"}), filename="my-lib.zip")
    assert response.status_code == 201
    assert response.json()["name"] == "my-lib"


def test_malicious_zip_rejected_and_cleaned_up(api, settings):
    response = upload(api, make_zip({"ok.py": "x = 1\n", "../../evil.py": "boom\n"}))
    assert response.status_code == 400
    assert "unsafe" in response.json()["detail"]
    assert api.get("/api/projects").json() == []
    assert workspace_entries(settings) == []


def test_zip_without_supported_files_rejected(api, settings):
    response = upload(api, make_zip({"image.png": b"\x89PNG\x00", "main.go": "package x\n"}))
    assert response.status_code == 422
    assert workspace_entries(settings) == []


def test_zip_upload_validation(api):
    assert upload(api, b"not a zip").status_code == 400
    assert upload(api, make_zip({"a.py": "1"}), filename="a.tar").status_code == 400
    assert api.post("/api/projects/import/zip", data={"name": "x"}).status_code == 422


def test_zip_upload_too_large(api):
    big = make_zip({"a.py": "1"}) + b"\x00" * (2 * 1024 * 1024)
    response = upload(api, big)
    assert response.status_code == 413


def test_list_and_get_projects(api, project):
    listing = api.get("/api/projects")
    assert listing.status_code == 200
    assert [p["id"] for p in listing.json()] == [project["id"]]
    assert listing.json()[0]["created_at"].endswith(("Z", "+00:00"))
    detail = api.get(f"/api/projects/{project['id']}")
    assert detail.status_code == 200
    assert detail.json()["file_count"] == 6


def test_list_files(api, project):
    response = api.get(f"/api/projects/{project['id']}/files")
    assert response.status_code == 200
    files = response.json()["files"]
    assert [f["path"] for f in files] == [
        "README.md", "app/main.py", "app/util.py", "config.yaml", "web/App.tsx", "web/index.ts"
    ]
    main = files[1]
    assert main["project_id"] == project["id"]
    assert main["language"] == "Python"
    assert main["line_count"] == 2
    assert main["size_bytes"] == len("def main():\n    return 42\n")
    assert len(main["content_hash"]) == 64


def test_file_content(api, project):
    response = api.get(
        f"/api/projects/{project['id']}/files/content", params={"path": "app/main.py"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "def main():\n    return 42\n"
    assert body["language"] == "Python"


@pytest.mark.parametrize(
    "path", ["missing.py", "../test.db", "..\\..\\test.db", ".env", "node_modules/lib/index.js"]
)
def test_file_content_not_found(api, project, path):
    response = api.get(f"/api/projects/{project['id']}/files/content", params={"path": path})
    assert response.status_code == 404


def test_overview(api, project):
    response = api.get(f"/api/projects/{project['id']}/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["project"]["id"] == project["id"]
    assert body["directory_count"] == 2
    assert {e["name"]: e["type"] for e in body["top_level"]} == {
        "app": "directory", "web": "directory", "README.md": "file", "config.yaml": "file"
    }
    python = next(s for s in body["languages"] if s["language"] == "Python")
    assert python == {"language": "Python", "files": 2, "lines": 3, "bytes": 31}
    assert len(body["largest_files"]) == 5


def test_delete_project_is_scoped(api, project, settings):
    other = upload(api, make_zip({"keep.py": "x = 1\n"})).json()
    response = api.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 204
    assert api.get(f"/api/projects/{project['id']}").status_code == 404
    assert not (Path(settings.workspace_dir) / project["id"]).exists()
    assert (Path(settings.workspace_dir) / other["id"] / "keep.py").is_file()
    assert [p["id"] for p in api.get("/api/projects").json()] == [other["id"]]
    assert api.get(f"/api/projects/{other['id']}/files").json()["files"][0]["path"] == "keep.py"


@pytest.mark.parametrize(
    "method,suffix",
    [("get", ""), ("delete", ""), ("get", "/files"), ("get", "/overview"),
     ("get", "/files/content?path=a.py")],
)
def test_missing_project_returns_404(api, method, suffix):
    for project_id in ("00000000-0000-0000-0000-000000000000", "..", "not-a-uuid"):
        response = getattr(api, method)(f"/api/projects/{project_id}{suffix}")
        assert response.status_code in (404, 405), (project_id, response.status_code)


# --- GitHub import (all HTTP traffic is mocked) ---------------------------------------------

def github_transport(handler):
    def override():
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            yield client

    return override


def repo_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.github.com" and request.url.path == "/repos/octo/demo":
        return httpx.Response(
            200, json={"full_name": "octo/demo", "default_branch": "main", "private": False}
        )
    archive_path = "/octo/demo/zip/refs/heads/main"
    if request.url.host == "codeload.github.com" and request.url.path == archive_path:
        return httpx.Response(200, content=make_zip(SAMPLE_REPO))
    return httpx.Response(404)


def test_github_import(api, settings):
    api.app.dependency_overrides[get_github_client] = github_transport(repo_handler)
    response = api.post("/api/projects/import/github", json={"url": "https://github.com/octo/demo"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "octo/demo"
    assert body["source_type"] == "github"
    assert body["source_url"] == "https://github.com/octo/demo"
    assert body["file_count"] == 6
    files = api.get(f"/api/projects/{body['id']}/files").json()["files"]
    assert "app/main.py" in [f["path"] for f in files]


def test_github_import_invalid_url_makes_no_request(api):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    api.app.dependency_overrides[get_github_client] = github_transport(handler)
    response = api.post("/api/projects/import/github", json={"url": "https://evil.com/octo/demo"})
    assert response.status_code == 400
    assert calls == []


@pytest.mark.parametrize(
    "response_factory,status",
    [
        (lambda r: httpx.Response(404), 404),
        (lambda r: httpx.Response(403, headers={"x-ratelimit-remaining": "0"}), 429),
        (lambda r: httpx.Response(500), 502),
        (lambda r: httpx.Response(301, headers={"location": "https://evil.example/x"}), 502),
    ],
)
def test_github_import_errors(api, settings, response_factory, status):
    api.app.dependency_overrides[get_github_client] = github_transport(response_factory)
    response = api.post("/api/projects/import/github", json={"url": "https://github.com/octo/demo"})
    assert response.status_code == status
    assert api.get("/api/projects").json() == []
    assert workspace_entries(settings) == []


def test_github_import_network_failures(api):
    def connect_error(request):
        raise httpx.ConnectError("boom", request=request)

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    for handler, status in ((connect_error, 502), (timeout, 504)):
        api.app.dependency_overrides[get_github_client] = github_transport(handler)
        response = api.post("/api/projects/import/github", json={"url": "https://github.com/o/r"})
        assert response.status_code == status


def test_github_download_size_limit(api, settings):
    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"full_name": "o/r", "default_branch": "main"})
        return httpx.Response(200, content=b"x" * (settings.max_download_bytes + 1))

    api.app.dependency_overrides[get_github_client] = github_transport(handler)
    response = api.post("/api/projects/import/github", json={"url": "https://github.com/o/r"})
    assert response.status_code == 413


def test_cors_allows_mutating_methods(api):
    response = api.options(
        "/api/projects/x",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "DELETE"},
    )
    assert response.status_code == 200
