# CodeAtlas AI — Milestone 1

A source-grounded codebase intelligence platform. Milestone 1 adds **repository ingestion and browsing**: import a public GitHub repository or a ZIP, explore its file tree, read syntax-highlighted source, and view repository statistics.

**Not yet implemented:** embeddings, vector indexing, search, and AI question answering (later milestones).

## Requirements
- Python 3.12+ (3.13 and 3.14 work with current dependencies)
- Node.js 22+
- npm
- Optional: Docker with Docker Compose

## Local setup — Windows PowerShell
The virtual environment can live in `backend\.venv` or at the project root (this checkout uses `.venv` at the root).
```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```
Open a **second** terminal:
```powershell
cd frontend
npm install
npm run dev
```
If Python 3.12 isn't installed, use an available compatible `py -3` version.

> **Upgrading from Milestone 0:** run `pip install -r requirements-dev.txt` (adds `python-multipart`), `alembic upgrade head` (adds revision `0002_project_files`, additive only), and `npm install` (adds `highlight.js`).

## Local setup — Linux / macOS
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```
In a separate terminal:
```bash
cd frontend
npm install
npm run dev
```

## URLs
- Frontend dashboard: http://localhost:3000
- API health: http://localhost:8000/health
- Database readiness: http://localhost:8000/ready
- Swagger documentation: http://localhost:8000/docs

## API
| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/projects` | List imported projects (newest first) with languages |
| GET | `/api/projects/{id}` | Project details |
| DELETE | `/api/projects/{id}` | Delete project, its manifest rows and its workspace folder (204) |
| POST | `/api/projects/import/github` | JSON `{"url": "https://github.com/owner/repo"}` → 201 project |
| POST | `/api/projects/import/zip` | `multipart/form-data` with `file` (.zip) and optional `name` → 201 project |
| GET | `/api/projects/{id}/files` | File manifest: path, language, size, line count, SHA-256, project ID |
| GET | `/api/projects/{id}/files/content?path=...` | Source of one manifest file |
| GET | `/api/projects/{id}/overview` | Totals, per-language stats, top-level structure, largest files |

Errors are returned as `{"detail": "..."}` with a meaningful status (400 invalid input/unsafe archive, 404 missing, 411/413 size, 422 no supported files, 429 GitHub rate limit, 502/504 GitHub network failure).

## Ingestion rules
- **Indexed languages:** Python, JavaScript, TypeScript, JSX, TSX, HTML, CSS, JSON, YAML, Markdown, SQL, TOML (by file extension). Other files are skipped.
- **Excluded:** `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `dist`, `build`, `.next`, tool caches; binary (NUL bytes / non-UTF-8) files; files over 1 MB; symlinks; credential files (`.env*`, `credentials.json`, `secrets.*`, `.npmrc`, `.pypirc`, `.netrc`, service-account JSON, …).
- **Archives are rejected** when they contain absolute paths or `..` traversal, are encrypted or malformed, exceed the entry count or total extracted size, or (uploads) exceed 20 MB.
- Imported code is **never executed**; no hooks, install scripts or dependencies are run.
- Files are stored under `backend/data/workspaces/<project_id>/`. Limits are configurable — see `.env.example`.

## Run tests and checks
```bash
cd backend
pytest -q
ruff check app tests
```
```bash
cd frontend
npm run lint     # tsc --noEmit
npm run build
```
The backend test suite mocks all GitHub HTTP traffic; no network access is required.

## Docker
From the project root:
```bash
docker compose up --build
```
The API container automatically runs Alembic migrations. SQLite data and imported workspaces persist in the `api_data` named volume.

## Configuration
Copy `.env.example` into `backend/.env` if changing defaults. The web app uses `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`). **Do not commit secrets**; these environment variables are development defaults.

## Known limitations
- Public GitHub repositories only, default branch only, unauthenticated (60 GitHub API requests/hour per IP). Repository archives over 50 MB are rejected.
- Only the indexed languages above are stored and browsable; e.g. `Dockerfile`, `.go` and `.txt` files are skipped.
- Imports run synchronously within the request; very large repositories may take a while.
- No authentication: every user of the API sees every project.

See `docs/` for the architecture, progress and decision log.
