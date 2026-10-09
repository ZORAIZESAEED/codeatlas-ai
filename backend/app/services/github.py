"""Public GitHub repository download.

Only two fixed hosts are ever contacted (api.github.com and codeload.github.com) and only
validated owner/repository names are placed in the request path, which prevents SSRF.
"""
import re
from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import quote, urlsplit

import httpx

from app.services.ingestion import IngestionError

API_BASE = "https://api.github.com"
CODELOAD_BASE = "https://codeload.github.com"
ALLOWED_HOSTS = {"github.com", "www.github.com"}
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")


@dataclass
class GitHubRepository:
    owner: str
    name: str
    default_branch: str
    html_url: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def _valid_owner_repo(owner: str, repo: str) -> bool:
    return bool(OWNER_RE.match(owner) and REPO_RE.match(repo)) and repo not in {".", ".."}


def parse_github_url(url: str) -> tuple[str, str]:
    """Return (owner, repo) for ``https://github.com/owner/repo`` style URLs."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise IngestionError("Invalid URL.") from exc
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS:
        raise IngestionError("Only https://github.com/<owner>/<repository> URLs are supported.")
    if parts.username or parts.password or port is not None or parts.query:
        raise IngestionError("GitHub URL must not contain credentials, ports or query strings.")
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) != 2:
        raise IngestionError("URL must point to a repository: https://github.com/<owner>/<repo>.")
    owner, repo = segments
    repo = repo.removesuffix(".git")
    if not _valid_owner_repo(owner, repo):
        raise IngestionError("Invalid GitHub owner or repository name.")
    return owner, repo


def _raise_for_github_error(response: httpx.Response) -> None:
    if response.status_code == 404:
        raise IngestionError("Repository not found or not public.", 404)
    if response.status_code == 429 or (
        response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
    ):
        raise IngestionError("GitHub rate limit exceeded. Please try again later.", 429)
    if response.status_code in (401, 403, 451):
        raise IngestionError("Repository is not accessible.", 403)
    raise IngestionError(f"GitHub returned an unexpected status ({response.status_code}).", 502)


def fetch_repository_info(client: httpx.Client, owner: str, repo: str) -> GitHubRepository:
    response = client.get(f"{API_BASE}/repos/{owner}/{repo}")
    # Renamed repositories answer with a redirect; follow it only within the API host.
    location = response.headers.get("location", "")
    if response.is_redirect and location.startswith(f"{API_BASE}/"):
        response = client.get(location)
    if response.status_code != 200:
        _raise_for_github_error(response)
    try:
        data = response.json()
        full_owner, full_repo = str(data["full_name"]).split("/", 1)
        branch = str(data["default_branch"])
        private = bool(data.get("private", False))
    except (ValueError, KeyError, TypeError) as exc:
        raise IngestionError("GitHub returned an unexpected response.", 502) from exc
    if private:
        raise IngestionError("Only public repositories are supported.", 403)
    valid_branch = bool(BRANCH_RE.match(branch)) and ".." not in branch
    if not _valid_owner_repo(full_owner, full_repo) or not valid_branch:
        raise IngestionError("GitHub returned unexpected repository metadata.", 502)
    return GitHubRepository(
        owner=full_owner,
        name=full_repo,
        default_branch=branch,
        html_url=f"https://github.com/{full_owner}/{full_repo}",
    )


def download_archive(
    client: httpx.Client, repo: GitHubRepository, out: BinaryIO, max_bytes: int
) -> None:
    """Stream the default-branch ZIP into ``out``, aborting once ``max_bytes`` is exceeded."""
    url = (
        f"{CODELOAD_BASE}/{repo.owner}/{repo.name}/zip/refs/heads/"
        f"{quote(repo.default_branch, safe='/')}"
    )
    limit_mb = max_bytes // (1024 * 1024)
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            _raise_for_github_error(response)
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise IngestionError(f"Repository archive exceeds {limit_mb} MB.", 413)
        received = 0
        for chunk in response.iter_bytes():
            received += len(chunk)
            if received > max_bytes:
                raise IngestionError(f"Repository archive exceeds {limit_mb} MB.", 413)
            out.write(chunk)
    out.seek(0)


def download_repository(
    client: httpx.Client, owner: str, repo: str, out: BinaryIO, max_bytes: int
) -> GitHubRepository:
    try:
        info = fetch_repository_info(client, owner, repo)
        download_archive(client, info, out, max_bytes)
    except httpx.TimeoutException as exc:
        raise IngestionError("Timed out while contacting GitHub.", 504) from exc
    except httpx.HTTPError as exc:
        raise IngestionError("Could not reach GitHub. Check your network connection.", 502) from exc
    return info
