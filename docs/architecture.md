# CodeAtlas architecture — Milestone 1

## Components
- **Frontend:** Next.js App Router. `src/app/page.tsx` renders a client-side dashboard (`src/components/`):
  - `Dashboard` — top bar with API status, project sidebar, main workspace.
  - `ImportPanel` — GitHub URL form and ZIP upload (drag & drop) with loading/error states.
  - `ProjectWorkspace` — project header, delete action, Overview/Files tabs, statistics.
  - `FileExplorer` — collapsible tree built from the manifest, filter box, file loading.
  - `CodeViewer` — line-numbered, syntax-highlighted source (`highlight.js` core with 10 registered grammars, `src/lib/highlight.ts`).
  - `src/lib/api.ts` — typed fetch client for the backend; surfaces backend `detail` messages.
- **API:** FastAPI (`app/main.py` factory). Routers: `app/api/health.py` (`/health`, `/ready`) and `app/api/projects.py` (`/api/projects/...`). Pydantic schemas live in `app/schemas.py`. `IngestionError` is mapped to JSON `{"detail"}` responses by an exception handler.
- **Services:**
  - `app/services/ingestion.py` — safe ZIP extraction, filtering, language detection, manifest generation, transactional import with cleanup, sandboxed file reads, workspace deletion.
  - `app/services/github.py` — GitHub URL validation, repository metadata lookup and size-limited archive streaming via `httpx`.
- **Database:** SQLAlchemy 2 + SQLite. A per-request `Session` comes from `app.db.session.get_db` (session factory created in the app lifespan).
  - `projects` — id, name, source_type (`github`/`zip`), source_url, status, created_at, file_count, total_lines, total_bytes, skipped_count.
  - `project_files` — manifest: project_id (FK), path, language, size_bytes, line_count, content_hash (SHA-256); unique `(project_id, path)`.
  - Alembic revisions: `0001_initial`, `0002_project_files` (additive).
- **Storage:** imported files live in `<WORKSPACE_DIR>/<project_id>/` (default `backend/data/workspaces`).

## Import flow
1. **GitHub:** validate URL (`https`, host `github.com`, `owner/repo` only) → `GET api.github.com/repos/{owner}/{repo}` (404 / rate limit / private handled) → stream `codeload.github.com/{owner}/{repo}/zip/refs/heads/{default_branch}` into a temp file, aborting above `MAX_DOWNLOAD_BYTES`.
   **ZIP:** check `Content-Length` before parsing multipart, accept one `.zip` file ≤ `MAX_UPLOAD_BYTES`.
2. Create `<workspace>/<new uuid>/`.
3. Validate every member name (no absolute paths, drive letters, `..`, NUL); reject encrypted archives and archives with too many entries.
4. Strip a single shared top-level folder; skip symlinks, excluded directories, unsupported extensions, sensitive files, Windows reserved names and oversized files.
5. Read each member with a hard byte cap (header sizes are not trusted), enforce the total extracted size, skip binary/non-UTF-8 content, write with exclusive-create (`xb`) after a resolved-path containment check.
6. Insert the project and manifest rows in one transaction. On any error the transaction is rolled back and the workspace folder is removed.

## Read & delete
- File content is served only for paths present in the manifest, then re-checked to resolve inside the project workspace.
- Delete removes manifest rows and the project in one transaction, then removes only `<workspace>/<project_id>` (the ID must be a UUID and its directory a direct child of the workspace root).

## Lifecycle
The engine opens at startup and is disposed at shutdown. Migrations run separately (`alembic upgrade head`); Docker runs them before launching Uvicorn. CORS allows the configured frontend origin with `GET`, `POST`, `DELETE`.

## Not implemented yet
Parsing/indexing (M2–M3), embeddings and vector search, RAG Q&A (M4). The project does **not** yet search or answer questions about repositories.
