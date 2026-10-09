# CodeAtlas AI — Milestone 2 (in progress on `feature/milestone-2-code-analysis`)

A source-grounded codebase intelligence platform.
- **Milestone 1 — repository ingestion and browsing:** import a public GitHub repository or a ZIP, explore its file tree, read syntax-highlighted source, and view repository statistics.
- **Milestone 2 — code analysis:** structural parsing (Python `ast`, Tree-sitter for JavaScript/JSX/TypeScript/TSX) extracts symbols, and every file is split into line-aligned chunks. Symbols and chunks are browsable in the file explorer.

**Not yet implemented:** embeddings, vector indexing (ChromaDB), search, and AI question answering (later milestones).

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

> **Upgrading:** run `pip install -r requirements-dev.txt`, `alembic upgrade head` and `npm install`.
> - From Milestone 0: adds `python-multipart`, `highlight.js` and revision `0002_project_files`.
> - From Milestone 1: adds `tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript` and revision `0003_code_analysis`. Both revisions are additive only.
> - Projects imported before Milestone 2 keep their files and show **analysis: pending**. Click **Analyze** in the project overview (or `POST /api/projects/{id}/analyze`) to parse them.

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

## Version control
The project is a Git repository (branch `main`). Generated data (`backend/data/`, SQLite files, workspaces), virtualenvs, `node_modules`, `.next`, caches and `.env` are ignored.

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
| GET | `/api/projects/{id}/overview` | Totals, per-language stats, top-level structure, largest files, analysis summary |
| POST | `/api/projects/{id}/analyze` | (Re)run code analysis; replaces previous symbols and chunks (idempotent) |
| GET | `/api/projects/{id}/symbols` | Symbols; filters `path`, `kind`, `q` (name contains), `limit` (≤ 1000), `offset` |
| GET | `/api/projects/{id}/chunks` | Chunks; filters `path`, `limit`, `offset`; `include_content=true` (requires `path`) adds chunk text |

Project responses include `analysis_status` (`pending` / `ready` / `failed`), `analyzed_at`, `symbol_count` and `chunk_count`. File entries include `parser` and `parse_error`.

Errors are returned as `{"detail": "..."}` with a meaningful status (400 invalid input/unsafe archive, 404 missing, 411/413 size, 422 no supported files, 429 GitHub rate limit, 502/504 GitHub network failure).

## Ingestion rules
- **Indexed file types** are defined in one registry, [`backend/app/services/languages.py`](backend/app/services/languages.py), and matched by exact file name, file-name pattern or extension. Defaults: Python, JavaScript, TypeScript, JSX, TSX, HTML, CSS/SCSS/Less, Shell, PowerShell, Ruby, PHP, Go, Rust, Java, Kotlin, C, C++, C#, Swift, JSON, YAML, TOML, XML, INI/cfg/conf, SQL, Dockerfile (`Dockerfile`, `Dockerfile.*`, `*.dockerfile`, `Containerfile`), Makefile, ignore files (`.gitignore`, `.dockerignore`, …), Markdown, and text (`.txt`, `.rst`, `LICENSE`, `README`, `CHANGELOG`, …). Unrecognised files are skipped.
- **Adding a file type:** append a `LanguageSpec(name, extensions, filenames, patterns)` to `DEFAULT_LANGUAGES`. For syntax highlighting, also map the name to a highlight.js grammar in `frontend/src/lib/highlight.ts`; unmapped types are shown as plain text. Existing projects must be re-imported to pick up new types.
- **Excluded directories:** VCS (`.git`, `.hg`, `.svn`), dependencies (`node_modules`, `bower_components`, `vendor`, `site-packages`, `.venv`, `venv`, `*.egg-info`), caches (`__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.tox`, `.cache`, `.gradle`, `.terraform`, `.idea`), build output (`dist`, `build`, `.next`, `out`, `target`, `coverage`).
- **Excluded files:** lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `poetry.lock`, `Cargo.lock`, …), minified bundles and source maps (`*.min.js`, `*.min.css`, `*.map`), binary (NUL bytes / non-UTF-8) files, files over 1 MB, symlinks, and credential files (`.env*` including `.env.example`, `secret(s).*`, `credential(s).*`, `.npmrc`, `.pypirc`, `.netrc`, `.pgpass`, `.git-credentials`, SSH keys, `*.pem`/`*.key`/`*.p12`/`*.pfx`/keystores, service-account JSON, …).
- **Archives are rejected** when they contain absolute paths or `..` traversal, are encrypted or malformed, exceed the entry count or total extracted size, or (uploads) exceed 20 MB.
- Imported code is **never executed**; no hooks, install scripts or dependencies are run.
- Files are stored under `backend/data/workspaces/<project_id>/`. Limits are configurable — see `.env.example`.

## Code analysis
Runs automatically after every import. It can be re-run with `POST /api/projects/{id}/analyze`.
- **Parsers** (`backend/app/services/parsing.py`, `PARSERS` map keyed by language name):
  - Python: standard-library `ast`.
  - JavaScript and JSX: Tree-sitter `javascript`. TypeScript: Tree-sitter `typescript`. TSX: Tree-sitter `tsx`.
  - Every other language uses the line-aware fallback with no symbols.
- **Symbols:**
  - Python: classes, functions and methods (nested classes included; function-local definitions skipped).
  - JS/TS: functions, arrow-function constants, classes, methods, interfaces, type aliases, enums and namespaces.
  - Each symbol has its qualified name, parent, line range (including decorators and the comment block directly above), first-line signature and first docstring/JSDoc line.
- **Chunks** never split a line:
  - Parsed files: one chunk per top-level symbol. A symbol over the limit is split by its child symbols, or by line windows if it has none. Code between symbols becomes `module` chunks.
  - Unparsed files: line windows that prefer blank lines (and Markdown headings outside code fences).
  - Small adjacent pieces are merged, and punctuation-only remnants (e.g. a closing `}`) join the previous chunk.
  - Limits: `CHUNK_MAX_LINES` (100) and `CHUNK_MAX_CHARS` (4000). A single line longer than the character limit is kept whole.
  - Lines end only at `\n` (CRLF files work). Form feeds, U+2028 and U+0085 inside a line don't shift line numbers. A UTF-8 BOM is ignored for parsing.
  - Chunk text is not duplicated in the database: chunks store line ranges, character count and SHA-256, and text is sliced from the workspace file on request.
- **Limits:** structural parsing is skipped (line chunking is still used, with a note in `parse_error`) for files over `PARSE_MAX_FILE_BYTES` (512 KB) and once a project has parsed `PARSE_MAX_TOTAL_BYTES` (50 MB). Line chunking is linear and always runs. Ingestion limits already cap files at 1 MB and projects at 200 MB.
- **Errors:** a syntax error or a too-deeply nested file falls back to line chunking and records `parse_error`. An unexpected error in one file only affects that file (line chunking, with a note). Tree-sitter's error-tolerant partial parses keep their symbols and also record a note. Only a project-level failure (e.g. a database error) marks analysis `failed`. The import stays `ready` and browsable, previous results are kept (one transaction), and **Re-analyze** retries.
- Parsing only builds syntax trees; imported code is never executed or imported.

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
- Only file types in the language registry are stored and browsable (e.g. `.vue`, `.svelte`, `.lua` are not registered yet).
- Exclusions are global defaults. `vendor`, `target` and `out` are always skipped, even when a repository keeps hand-written source there. Repository-specific exclusion settings may be added later.
- Imports run synchronously within the request; very large repositories may take a while.
- No authentication: every user of the API sees every project.
- Structural parsing covers Python and JavaScript/TypeScript only; other languages (Go, Rust, Java, …) are chunked by lines without symbols. CommonJS assignments such as `module.exports = function () {}` are not reported as symbols.
- Analysis runs synchronously after import and on `/analyze`; there is no background job queue yet.

See `docs/` for the architecture, progress and decision log.
