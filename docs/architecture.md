# CodeAtlas architecture — Milestones 1–2

## Components
- **Frontend:** Next.js App Router. `src/app/page.tsx` renders a client-side dashboard (`src/components/`):
  - `Dashboard` — top bar with API status, project sidebar, main workspace.
  - `ImportPanel` — GitHub URL form and ZIP upload (drag & drop) with loading/error states.
  - `ProjectWorkspace` — project header, delete action, Overview/Files tabs, statistics, code-analysis card (status, symbols by kind, files by parser, parse issues, Analyze/Re-analyze).
  - `FileExplorer` — collapsible tree built from the manifest, filter box, file loading.
  - `OutlinePanel` — per-file Symbols/Chunks lists; selecting one highlights and scrolls to its line range in the viewer.
  - `CodeViewer` — line-numbered, syntax-highlighted source (`highlight.js` core; `src/lib/highlight.ts` maps backend language names to grammars, unmapped types render as plain text).
  - `src/lib/api.ts` — typed fetch client for the backend; surfaces backend `detail` messages.
- **API:** FastAPI (`app/main.py` factory). Routers: `app/api/health.py` (`/health`, `/ready`) and `app/api/projects.py` (`/api/projects/...`). Pydantic schemas live in `app/schemas.py`. `IngestionError` is mapped to JSON `{"detail"}` responses by an exception handler.
- **Services:**
  - `app/services/languages.py` — `LanguageRegistry` of `LanguageSpec`s (exact file names → file-name globs → extensions, case-insensitive). `DEFAULT_REGISTRY` is the single source of truth for which file types are indexed.
  - `app/services/ingestion.py` — safe ZIP extraction, exclusion rules (directories, generated files, credentials), manifest generation, transactional import with cleanup, sandboxed file reads, workspace deletion. `extract_archive` accepts an optional registry.
  - `app/services/parsing.py` — `parse_source(text, language)` → `ParseResult(parser, symbols, error)`; never raises. `PARSERS` maps language names to parsers: Python `ast`, Tree-sitter grammars for JavaScript/JSX (`javascript`), TypeScript (`typescript`) and TSX (`tsx`). Unregistered languages and unparsable files return the `line-fallback` result.
  - `app/services/chunking.py` — `chunk_source(...)` → line-aligned `Chunk`s (symbol-aware for parsed files, line windows otherwise).
  - `app/services/analysis.py` — `analyze_project` reads each manifest file through the sandboxed reader, parses (within `PARSE_MAX_FILE_BYTES` / `PARSE_MAX_TOTAL_BYTES`), chunks, and replaces the project's symbols/chunks in one transaction. Each file is isolated, so an unexpected error degrades that file to line chunking. `run_analysis` rolls back and marks the project `failed` on project-level errors, keeping previous results. It returns `None` if the project was deleted meanwhile (API answers 404).
  - `app/services/github.py` — GitHub URL validation, repository metadata lookup and size-limited archive streaming via `httpx`.
- **Database:** SQLAlchemy 2 + SQLite. A per-request `Session` comes from `app.db.session.get_db` (session factory created in the app lifespan).
  - `projects` — id, name, source_type (`github`/`zip`), source_url, status, created_at, file_count, total_lines, total_bytes, skipped_count, analysis_status (`pending`/`ready`/`failed`), analyzed_at, symbol_count, chunk_count.
  - `project_files` — manifest: project_id (FK), path, language, size_bytes, line_count, content_hash (SHA-256), parser, parse_error; unique `(project_id, path)`.
  - `code_symbols` — project_id (FK), path, name, qualified_name, kind, parent, start_line, end_line, signature, docstring; index `(project_id, path)`.
  - `code_chunks` — project_id (FK), path, chunk_index, start_line, end_line, kind (`symbol`/`module`/`text`), symbol, language, char_count, content_hash; unique `(project_id, path, chunk_index)`. Chunk text is not stored; it is sliced from the workspace file when requested.
  - Alembic revisions: `0001_initial`, `0002_project_files`, `0003_code_analysis` (all additive after the first).
- **Storage:** imported files live in `<WORKSPACE_DIR>/<project_id>/` (default `backend/data/workspaces`).

## Import flow
1. **GitHub:** validate URL (`https`, host `github.com`, `owner/repo` only) → `GET api.github.com/repos/{owner}/{repo}` (404 / rate limit / private handled) → stream `codeload.github.com/{owner}/{repo}/zip/refs/heads/{default_branch}` into a temp file, aborting above `MAX_DOWNLOAD_BYTES`.
   **ZIP:** check `Content-Length` before parsing multipart, accept one `.zip` file ≤ `MAX_UPLOAD_BYTES`.
2. Create `<workspace>/<new uuid>/`.
3. Validate every member name (no absolute paths, drive letters, `..`, NUL); reject encrypted archives and archives with too many entries.
4. Strip a single shared top-level folder; skip symlinks, excluded directories, file types not in the language registry, sensitive files, generated files (lockfiles, minified bundles), Windows reserved names and oversized files.
5. Read each member with a hard byte cap (header sizes are not trusted), enforce the total extracted size, skip binary/non-UTF-8 content, write with exclusive-create (`xb`) after a resolved-path containment check.
6. Insert the project and manifest rows in one transaction. On any error the transaction is rolled back and the workspace folder is removed.
7. Run code analysis in a second transaction. A failure here does not undo the import; the project is marked `failed` and can be re-analyzed.

## Read & delete
- File content is served only for paths present in the manifest, then re-checked to resolve inside the project workspace.
- Delete removes symbols, chunks, manifest rows and the project in one transaction, then removes only `<workspace>/<project_id>` (the ID must be a UUID and its directory a direct child of the workspace root).

## Lifecycle
The engine opens at startup and is disposed at shutdown. Migrations run separately (`alembic upgrade head`); Docker runs them before launching Uvicorn. CORS allows the configured frontend origin with `GET`, `POST`, `DELETE`.

## Not implemented yet
Embeddings and vector indexing (ChromaDB), search (M3), RAG Q&A (M4). Chunks are prepared for embedding but nothing is embedded or searched yet. The project does **not** yet search or answer questions about repositories.
