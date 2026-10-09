import io
import zipfile

import pytest

from app.core.config import Settings
from app.services.github import parse_github_url
from app.services.ingestion import (
    IngestionError,
    count_lines,
    detect_language,
    extract_archive,
    is_sensitive,
)
from tests.helpers import SAMPLE_REPO, make_zip


@pytest.fixture
def limits(tmp_path) -> Settings:
    return Settings(workspace_dir=str(tmp_path / "ws"), max_indexed_file_bytes=1000)


def test_extract_filters_unsupported_and_strips_root(tmp_path, limits):
    result = extract_archive(io.BytesIO(make_zip(SAMPLE_REPO)), tmp_path / "out", limits)
    paths = sorted(f.path for f in result.files)
    assert paths == [
        "README.md", "app/main.py", "app/util.py", "config.yaml", "web/App.tsx", "web/index.ts"
    ]
    # node_modules, .git, .env, binary png, and NUL-containing .py are excluded
    assert result.skipped == 5
    assert not (tmp_path / "out" / "node_modules").exists()
    assert not (tmp_path / "out" / ".env").exists()
    main = next(f for f in result.files if f.path == "app/main.py")
    assert main.language == "Python" and main.line_count == 2 and len(main.content_hash) == 64


@pytest.mark.parametrize(
    "name", ["../evil.py", "a/../../evil.py", "/etc/evil.py", "C:/evil.py", "..\\evil.py"]
)
def test_extract_rejects_path_traversal(tmp_path, limits, name):
    data = make_zip({"ok.py": "x = 1\n", name: "boom = 1\n"})
    with pytest.raises(IngestionError, match="unsafe"):
        extract_archive(io.BytesIO(data), tmp_path / "out", limits)
    assert not (tmp_path / "evil.py").exists()


def test_extract_skips_symlinks(tmp_path, limits):
    data = make_zip({"a.py": "x = 1\n"}, symlinks={"link.py": "/etc/passwd"})
    result = extract_archive(io.BytesIO(data), tmp_path / "out", limits)
    assert [f.path for f in result.files] == ["a.py"]
    assert result.skipped == 1


def test_extract_skips_oversized_files(tmp_path, limits):
    data = make_zip({"big.py": "x" * 2000, "small.py": "y = 1\n"})
    result = extract_archive(io.BytesIO(data), tmp_path / "out", limits)
    assert [f.path for f in result.files] == ["small.py"]


def test_extract_enforces_total_size_limit(tmp_path):
    settings = Settings(max_indexed_file_bytes=1000, max_extracted_bytes=1500)
    data = make_zip({"a.py": "a" * 900, "b.py": "b" * 900})
    with pytest.raises(IngestionError) as exc:
        extract_archive(io.BytesIO(data), tmp_path / "out", settings)
    assert exc.value.status_code == 413


def test_extract_enforces_entry_limit(tmp_path):
    settings = Settings(max_archive_entries=2)
    data = make_zip({"a.py": "1", "b.py": "2", "c.py": "3"})
    with pytest.raises(IngestionError, match="too many entries"):
        extract_archive(io.BytesIO(data), tmp_path / "out", settings)


def test_extract_rejects_encrypted(tmp_path, limits):
    data = bytearray(make_zip({"a.py": "x = 1\n"}))
    # zipfile cannot write encrypted entries, so set the "encrypted" flag bit by hand
    # in both the local file header and the central directory header.
    data[data.find(b"PK\x03\x04") + 6] |= 0x1
    data[data.find(b"PK\x01\x02") + 8] |= 0x1
    assert zipfile.ZipFile(io.BytesIO(bytes(data))).infolist()[0].flag_bits & 0x1
    with pytest.raises(IngestionError, match="Encrypted"):
        extract_archive(io.BytesIO(bytes(data)), tmp_path / "out", limits)


def test_extract_rejects_malformed(tmp_path, limits):
    with pytest.raises(IngestionError, match="not a valid ZIP"):
        extract_archive(io.BytesIO(b"definitely not a zip"), tmp_path / "out", limits)


def test_language_and_sensitive_detection():
    assert detect_language("a.tsx") == "TSX"
    assert detect_language("A.PY") == "Python"
    assert detect_language("pyproject.toml") == "TOML"
    assert detect_language("main.go") is None
    assert detect_language("Makefile") is None
    assert is_sensitive(".env") and is_sensitive(".env.local") and is_sensitive("credentials.json")
    assert not is_sensitive("package.json")


def test_count_lines():
    assert count_lines("") == 0
    assert count_lines("a") == 1
    assert count_lines("a\nb\n") == 2


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("https://github.com/owner/repo/", ("owner", "repo")),
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://www.github.com/my-org/my.repo_1", ("my-org", "my.repo_1")),
    ],
)
def test_parse_github_url_valid(url, expected):
    assert parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/repo",
        "https://gitlab.com/owner/repo",
        "https://github.com.evil.com/owner/repo",
        "https://user:pass@github.com/owner/repo",
        "https://github.com:8443/owner/repo",
        "https://github.com/owner",
        "https://github.com/owner/repo/tree/main",
        "https://github.com/owner/repo?x=1",
        "https://github.com/-bad/repo",
        "https://github.com/owner/..",
        "file:///etc/passwd",
        "https://127.0.0.1/owner/repo",
        "not a url",
    ],
)
def test_parse_github_url_invalid(url):
    with pytest.raises(IngestionError):
        parse_github_url(url)
