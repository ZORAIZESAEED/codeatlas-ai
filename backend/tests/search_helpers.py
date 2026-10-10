import sqlite3
from pathlib import Path

from tests.helpers import make_zip

AUTH_PY = '''"""Authentication helpers."""
import hashlib


class AuthService:
    """Authenticate users against the user store."""

    def __init__(self, users):
        self.users = users

    def authenticate(self, username, password):
        user = self.users.get(username)
        if user is None or user["hash"] != hash_password(password):
            raise LoginError("invalid credentials")
        return user


class LoginError(Exception):
    """Raised when a login attempt fails."""


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def create_access_token(user_id, secret):
    """Create a signed JWT access token for the user."""
    header = {"alg": "HS256", "typ": "JWT"}
    return encode_jwt(header, {"sub": user_id}, secret)


def encode_jwt(header, payload, secret):
    return f"{header}.{payload}.{secret}"
'''

DATABASE_PY = '''"""Database connection setup."""


def init_db(url):
    """Initialize the database engine and connection pool."""
    engine = {"url": url, "pool": 5}
    return engine


def close_db(engine):
    engine.clear()
'''

API_TS = '''export interface LoginRequest {
  username: string;
  password: string;
}

export function createAccessToken(userId: string): string {
  return `token-${userId}`;
}

export class SessionStore {
  private sessions = new Map<string, string>();

  save(userId: string): string {
    const token = createAccessToken(userId);
    this.sessions.set(token, userId);
    return token;
  }
}
'''

VALIDATE_JS = '''function validateRequest(body) {
  if (!body || typeof body.username !== "string") {
    throw new Error("username is required");
  }
  return true;
}

module.exports = { validateRequest };
'''

SEARCH_REPO = {
    "demo/app/auth.py": AUTH_PY,
    "demo/app/database.py": DATABASE_PY,
    "demo/web/api.ts": API_TS,
    "demo/web/validate.js": VALIDATE_JS,
    "demo/README.md": "# Demo\n\nA tiny service with login and JWT tokens.\n",
}


def import_repo(api, files: dict[str, str] | None = None, name: str = "demo") -> dict:
    response = api.post(
        "/api/projects/import/zip",
        files={"file": (f"{name}.zip", make_zip(files or SEARCH_REPO), "application/zip")},
        data={"name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def index(api, project_id: str) -> dict:
    response = api.post(f"/api/projects/{project_id}/index")
    assert response.status_code == 200, response.text
    return response.json()


def search(api, project_id: str, query: str, **body) -> dict:
    response = api.post(f"/api/projects/{project_id}/search", json={"query": query, **body})
    assert response.status_code == 200, response.text
    return response.json()


def db_rows(settings, sql: str, *params):
    path = settings.database_url.removeprefix("sqlite:///")
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, params).fetchall()


def chroma_ids(settings, project_id: str) -> set[str]:
    from app.services.indexing.documents import EMBEDDING_INPUT_VERSION
    from app.services.indexing.vector_store import VectorCollection, collection_name

    (name,) = db_rows(
        settings, "SELECT collection_name FROM search_indexes WHERE project_id = ?", project_id
    )[0]
    # A first run that failed has no recorded collection yet: use the configured one.
    name = name or collection_name(project_id, "fake", "fake-hash-64", 64, EMBEDDING_INPUT_VERSION)
    return VectorCollection(settings, name, create=False).all_ids()


def workspace_file(settings, project_id: str, path: str) -> Path:
    return Path(settings.workspace_dir) / project_id / path


def write_lf(path: Path, text: str) -> None:
    """Simulate a source edit without Windows newline translation."""
    path.write_bytes(text.encode("utf-8"))
