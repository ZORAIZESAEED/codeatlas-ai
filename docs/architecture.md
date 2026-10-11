# CodeAtlas architecture — Milestones 1–3

## Components
- **Frontend:** Next.js App Router. `src/app/page.tsx` renders a client-side dashboard (`src/components/`):
  - `Dashboard` — top bar with API status, project sidebar, main workspace.
  - `ImportPanel` — GitHub URL form and ZIP upload (drag & drop) with loading/error states.
  - `ProjectWorkspace` — project header, delete action, Overview/Files/Search tabs, statistics, code-analysis card (status, symbols by kind, files by parser, parse issues, Analyze/Re-analyze), search-index card (`IndexCard`, see Milestone 3).
  - `FileExplorer` — collapsible tree built from the manifest, filter box, file loading.
  - `OutlinePanel` — per-file Symbols/Chunks lists; selecting one highlights and scrolls to its line range in the viewer.
  - `CodeViewer` — line-numbered, syntax-highlighted source (`highlight.js` core; `src/lib/highlight.ts` maps backend language names to grammars, unmapped types render as plain text).
  - `src/lib/api.ts` — typed fetch client for the backend; surfaces backend `detail` messages.
- **API:** FastAPI (`app/main.py` factory). Routers: `app/api/health.py` (`/health`, `/ready`), `app/api/projects.py` (`/api/projects/...`) and `app/api/search.py` (index and search endpoints, Milestone 3). Pydantic schemas live in `app/schemas.py`. `IngestionError` is mapped to JSON `{"detail"}` responses by an exception handler.
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
- Delete removes search-index rows (manifest + FTS + state), symbols, chunks, manifest rows and the project in one transaction, then removes only `<workspace>/<project_id>` and finally the project's ChromaDB collections (the ID must be a UUID and its directory a direct child of the workspace root).

## Lifecycle
The engine opens at startup and is disposed at shutdown. At startup, any search index left in `indexing` by a stopped server is marked `failed` (interrupted); this assumes a single API process. Migrations run separately (`alembic upgrade head`); Docker runs them before launching Uvicorn. CORS allows the configured frontend origin with `GET`, `POST`, `DELETE`.

## Milestone 3 — retrieval
### Components
- **API:** `app/api/search.py` provides `POST /{id}/index`, `GET /{id}/index-status` and `POST /{id}/search`. `DELETE /{id}` in `projects.py` also removes index rows (same transaction) and then the vector collections (after commit).
- **`app/services/embeddings/`**
  - `base.py`: `EmbeddingProvider` (`embed_documents`, `embed_query`, `model_identifier`, `embedding_dimension`) validates counts, dimensions and finiteness.
  - `local_provider.py`: fastembed/ONNX on CPU, loaded lazily under a lock, with per-model query/document prefixes.
  - `fake_provider.py`: deterministic hashing embeddings for tests.
  - `registry.py`: cached providers per configuration. A project's recorded model can be requested explicitly.
- **`app/services/indexing/`**
  - `documents.py`: `ChunkRef`, deterministic `record_id`, versioned `embedding_text` / `embed_hash`, FTS fields with identifier sub-words.
  - `vector_store.py`: ChromaDB `PersistentClient` (telemetry off), collection naming, batched upsert/get/update/delete/query, project and orphan collection cleanup.
  - `manifest.py`: `index_entries` and `chunk_fts` writes.
  - `status.py`: derives the effective status (`pending` / `indexing` / `ready` / `stale` / `failed`).
  - `indexer.py`: the orchestrator.
- **`app/services/retrieval/`**
  - `terms.py`: identifier splitting, query terms, identifier/path detection and match rules.
  - `keyword.py`: exact identifier/path retriever and FTS5 BM25 retriever.
  - `semantic.py`: ChromaDB retriever.
  - `fusion.py`: weighted RRF.
  - `reranker.py`: `Reranker` protocol and `FeatureReranker`.
  - `models.py`: shared types and SQL filters.
  - `hybrid.py`: the search pipeline.
- **`app/services/evaluation/`**: `metrics.py` (Recall@K, Precision@K, Hit@K, MRR) and `benchmark.py` (CLI runner over `backend/benchmarks/`).
- **Frontend:**
  - `IndexCard` (overview): status pill, model, counts, real progress via polling, Build/Update/Reindex.
  - `SearchPanel` (Search tab): query, mode selector, language/symbol-type/directory filters, results with highlighted line-numbered snippets, method badges, fallback/stale warnings.
  - Clicking a result remounts `FileExplorer` with `initialPath` + `initialRange`, so `CodeViewer` highlights and scrolls to the cited lines.

### Data
- `projects.analysis_revision` is incremented by every successful analysis.
- `search_indexes` (one row per project) stores:
  - status;
  - provider, model, dimension, input version and collection name;
  - the analysis revision it was built from and the indexed chunk count;
  - last-run counters (total, processed, embedded, reused, removed, skipped);
  - started/indexed timestamps, duration and error.
- `index_entries` is the manifest. Columns: `id` (AUTOINCREMENT, also the FTS rowid), project_id, `record_id` (ChromaDB id, unique per project), `embed_hash`, path, start/end line, chunk kind, symbol, symbol kind, language, content hash. Indexes: `(project_id, path)` and `(project_id, embed_hash)`.
- `chunk_fts` is a contentless FTS5 table (`project, path, symbol, body`, `unicode61` with `_` as a token character). The `project` column holds a per-project token, so MATCH is restricted to one project.
- ChromaDB holds one cosine collection per project × provider × model × dimension × input version, with the metadata listed in the README. Vectors are not stored in SQLite.
- Alembic revision `0004_search_index` (additive).

### Indexing flow (`indexer.index_project`)
1. The project must exist and analysis must be `ready`. An in-process guard allows one run per project (409 otherwise). Status `indexing` is committed.
2. Load chunk refs from `code_chunks`, joined to `code_symbols` for the symbol kind. Enforce `INDEX_MAX_CHUNKS`.
3. Sweep orphan collections. Open or create the target collection.
4. Reconcile ChromaDB ids with the manifest. Diff by `record_id` into kept, added and removed sets. Kept records get metadata refreshed without re-embedding.
5. Read added chunks one file at a time and verify their SHA-256 (mismatches are skipped and counted). Build the embedding text, then per batch: reuse a vector (same record id already in Chroma, or same `embed_hash`) or embed. Upsert, commit progress counters, check `INDEX_TIMEOUT_SECONDS`.
6. In one SQLite transaction: delete removed entries and FTS rows, insert new entries and FTS rows, record model/revision/counts and mark `ready`.
7. Delete obsolete vectors, and the previous model's collection if the model changed. A failure here is logged and repaired by the next run.

Any exception before step 6 commits rolls back SQLite and marks the run `failed`. The previous index stays usable.

### Search flow (`hybrid.search`)
1. Normalize the query (printable characters, collapsed whitespace, ≤ 500 chars) and clamp the limit. Return 404 for an unknown project and 409 if nothing is indexed.
2. Run the retrievers for the mode, each with the same filters and a candidate pool (`SEARCH_CANDIDATE_POOL`):
   - exact (identifiers/paths);
   - FTS5 BM25 with column weights path 3, symbol 4, body 1;
   - semantic (query embedded with the index's model; record ids mapped through the manifest; unknown ids dropped).
3. Weighted RRF with query-aware keyword weight. Deduplicate by entry; ties break by best rank, then entry id.
4. Source validation: the file must be in the manifest and its current text must hash to the indexed content hash. Overlapping ranges in a file keep the best-ranked one.
5. Optional reranking, then truncate to `limit`. The response carries snippets, citations, warnings, fusion weights, candidate counts and per-stage timings.

## Not implemented yet
Source-grounded, LLM-generated answers (M4). Search returns ranked source chunks only.
