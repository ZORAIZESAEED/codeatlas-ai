# CodeAtlas AI — Milestones 1–3 (on `main`)

A source-grounded codebase intelligence platform.
- **Milestone 1 — repository ingestion and browsing:** import a public GitHub repository or a ZIP, explore its file tree, read syntax-highlighted source, and view repository statistics.
- **Milestone 2 — code analysis:** structural parsing (Python `ast`, Tree-sitter for JavaScript/JSX/TypeScript/TSX) extracts symbols, and every file is split into line-aligned chunks. Symbols and chunks are browsable in the file explorer.
- **Milestone 3 — code search:** local embeddings (fastembed, CPU), a ChromaDB vector index, SQLite FTS5 keyword search and exact identifier/path lookup, fused into hybrid search. Results are real source chunks with file path, symbol, line range and the retrieval methods that found them; clicking one opens the code viewer at those lines.

**Not yet implemented:** conversational, LLM-generated answers (Milestone 4). Search returns source; it does not write answers.

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
> - From Milestone 2: adds `chromadb` and `fastembed` and revision `0004_search_index` (additive: `search_indexes`, `index_entries`, the `chunk_fts` FTS5 table and `projects.analysis_revision`). Existing projects keep their chunks and show **Search index: Not indexed**. Click **Build index** in the overview; no re-import or re-analysis is needed.
>
> **First indexing (Windows):** the first **Build index** (or `POST /index`) downloads the embedding model `BAAI/bge-small-en-v1.5` (~67 MB) from Hugging Face into `backend\data\models`. Later runs work offline. Nothing is downloaded when the server starts. Expect a few chunks per second on CPU (measured on the development machine, Intel Skylake with 8 logical CPUs: 1,027 chunks in 200 s, 248 chunks in 130 s); re-indexing unchanged code takes well under a second. A GPU is not needed.

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
| DELETE | `/api/projects/{id}` | Delete project, its manifest rows, search index (SQLite + ChromaDB) and its workspace folder (204) |
| POST | `/api/projects/import/github` | JSON `{"url": "https://github.com/owner/repo"}` → 201 project |
| POST | `/api/projects/import/zip` | `multipart/form-data` with `file` (.zip) and optional `name` → 201 project |
| GET | `/api/projects/{id}/files` | File manifest: path, language, size, line count, SHA-256, project ID |
| GET | `/api/projects/{id}/files/content?path=...` | Source of one manifest file |
| GET | `/api/projects/{id}/overview` | Totals, per-language stats, top-level structure, largest files, analysis summary |
| POST | `/api/projects/{id}/analyze` | (Re)run code analysis; replaces previous symbols and chunks (idempotent) |
| GET | `/api/projects/{id}/symbols` | Symbols; filters `path`, `kind`, `q` (name contains), `limit` (≤ 1000), `offset` |
| GET | `/api/projects/{id}/chunks` | Chunks; filters `path`, `limit`, `offset`; `include_content=true` (requires `path`) adds chunk text |
| POST | `/api/projects/{id}/index` | Build or incrementally update the search index (synchronous; returns the index status). 409 if analysis is not ready or a run is in progress |
| GET | `/api/projects/{id}/index-status` | `status` (`pending` / `indexing` / `ready` / `stale` / `failed`), stale reasons, error, embedding provider/model/dimension, indexed vs. analyzed chunk counts, last-run counters (`processed_chunks` / `total_chunks`, embedded, reused, removed, skipped), `last_indexed_at`, `duration_ms` |
| POST | `/api/projects/{id}/search` | Code search (see below) |

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

## Code search (Milestone 3)
**Workflow:** import → analysis (automatic) → **Build index** in the project overview → **Search** tab. Click a result's path or snippet to open the file with the cited lines highlighted.

**Indexing lifecycle.** `POST /index` reuses the Milestone 2 chunks and compares them with the index manifest (`index_entries`):
- Unchanged chunks are kept without re-embedding. New or changed chunks are embedded in batches. A chunk whose identical content only moved reuses its vector. Removed chunks are deleted from ChromaDB and FTS.
- Re-analysis makes the index **stale**, as does changing `EMBEDDING_MODEL` or the embedding input format. Click **Update index** (or call `POST /index` again).
- A failed run keeps the previous index searchable and reports `failed` with the error, plus stale reasons if that previous index is also outdated. It is never marked ready. The next run reconciles any partial writes.
- Closing the browser or cancelling the request does not stop a run; it finishes server-side. If the server stops mid-run, startup marks the run `failed` ("interrupted"), so the UI never stays stuck on *Indexing*. Click **Build index** / **Reindex** again; vectors already written are reused.
- Indexing runs **synchronously** in the request thread. Progress is real (`processed_chunks` / `total_chunks`) and the UI polls it.
- Limits: `INDEX_MAX_CHUNKS` (50,000) and `INDEX_TIMEOUT_SECONDS` (1,800).

**ChromaDB persistence.** Vectors live in `CHROMA_DIR` (default `backend/data/chroma`, git-ignored), in one collection per project and embedding model (`ca_<project>_<model signature>`). Each record carries project ID, path, language, symbol, symbol kind, chunk kind, start/end line, content hash, analysis revision, embedding model and input version. Record IDs are deterministic. Deleting a project removes its collection. Deleting `backend/data/chroma` loses only vectors: run **Build index** again.

**Retrieval modes** (`POST /api/projects/{id}/search`):

```json
{"query": "Where is authentication implemented?", "mode": "hybrid", "limit": 10,
 "language": null, "file_path": null, "directory": null, "symbol_type": null, "rerank": null}
```
- `keyword`:
  - *Exact* lookup of identifiers and paths named in the query (`AuthService`, `create_access_token`, `core/database.py`).
  - SQLite FTS5 BM25 over paths, symbol names and source text. `createAccessToken` is also indexed as `create access token`.
- `semantic`: cosine similarity of the query embedding against the project's ChromaDB collection.
- `hybrid` (default): weighted Reciprocal Rank Fusion of exact, keyword and semantic ranks (`RRF_K=60`). Keyword weight is 1.0 when the query contains code identifiers and 0.25 for plain questions. If embeddings are unavailable, hybrid returns keyword results labelled `mode_used: "keyword"` with a `fallback_reason`; `semantic` mode returns 503.
- `rerank: true` applies an optional deterministic lexical reranker. It is off by default because it did not improve the benchmark.

Filters (`language`, `file_path`, `directory`, `symbol_type` = function/method/class/… or module/text) match manifest values only. `limit` is 1–50.

The response contains:
- `mode_requested`, `mode_used`, `fallback_reason`, `fusion_weights` and the full `index_status`.
- `results`: `chunk_id`, `rank`, `file_path`, `symbol_name`, `symbol_type`, `language`, `start_line`, `end_line`, `snippet` (≤ 40 lines / 2,400 chars), `retrieval_methods`, per-method ranks, raw scores, `score`.
- `citations` (`path:start-end`), `warnings` (stale/failed index, skipped sources), candidate counts and `timings_ms`.

`score` orders results; it is **not** a confidence. Every result is re-validated against the current file (SHA-256) before it is returned.

**Embedding models.** The default is `BAAI/bge-small-en-v1.5` (384-d, ~67 MB, CPU). Alternatives are set via `EMBEDDING_MODEL` (any fastembed text model):
- `sentence-transformers/all-MiniLM-L6-v2`: faster on CPU, lower benchmark quality.
- `jinaai/jina-embeddings-v2-base-code`: code-aware, ~640 MB, slow on CPU.

Changing the model marks indexes stale; reindexing builds a new collection and deletes the old one. Embedding runs locally; source code is never sent to an external service.

## Retrieval benchmark
```powershell
cd backend
python -m app.services.evaluation.benchmark                       # default model, writes benchmarks\results\
python -m app.services.evaluation.benchmark --model sentence-transformers/all-MiniLM-L6-v2
python -m app.services.evaluation.benchmark --provider fake --model fake-hash-64 --no-write   # no model download
```
The runner imports the controlled repositories in `backend/benchmarks/repos` into a **temporary** database and vector store (never your data), indexes them with the real pipeline, and runs the 41 labelled queries in `backend/benchmarks/queries.json`. Each query runs in four configurations (keyword, semantic, hybrid, hybrid+rerank) against the same labels. The output reports Recall@1/3/5/10, Precision@K, MRR, latency, and breakdowns per category and repository, and lists failing queries. Recorded results are in `backend/benchmarks/results/`.

Default model, 2026-10-10, CPU:

| Configuration | Recall@5 | MRR | Mean latency |
| --- | --- | --- | --- |
| keyword | 0.750 | 0.646 | 16.7 ms |
| semantic | 0.927 | 0.933 | 45.7 ms |
| hybrid | 0.878 | 0.888 | 49.6 ms |
| hybrid + rerank | 0.866 | 0.837 | 51.9 ms |

**Choosing a mode (trade-off).** On these natural-language-heavy queries, semantic-only retrieval currently scores best (MRR 0.933 vs 0.888 for hybrid). The default stays **hybrid** because 41 queries over two small repositories are too few to justify dropping the other signals:
- hybrid guarantees exact identifier and file-path hits;
- it covers code the embedding window truncates (keyword search sees whole chunks);
- it still works, as a labelled keyword fallback, when the embedding model is unavailable.

Use **Semantic** for conceptual questions, and **Keyword** for identifiers, strings, error messages and config keys. The UI always shows the selected mode with a one-line description, and each result list states the mode used and the retrievers that actually ran. See `docs/decisions.md` (38–40) and the results files for failing queries.

## Run tests and checks
```bash
cd backend
pytest -q
ruff check app tests
```
Tests use a deterministic fake embedding provider and temporary ChromaDB/SQLite storage. They never download model weights.
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
- **Search (Milestone 3):**
  - Indexing is synchronous and CPU-bound: about 2–5 chunks per second on the development machine, so a 10,000-chunk repository takes tens of minutes. One indexing run per project at a time, guarded per process. Running several Uvicorn workers would need a cross-process lock.
  - Semantic vectors cover only the first `EMBEDDING_MAX_INPUT_CHARS` (2,000) characters of a chunk (the model's 512-token window). Keyword search covers the whole chunk.
  - Generated code, tests and docs are indexed like hand-written code. In live tests, generated API clients (`*.gen.ts`), test helpers and Markdown docs often outranked the implementation for broad questions. There is no path-based boosting or exclusion yet.
  - Symbols come from Milestone 2. Code without extracted symbols (e.g. `module.exports = class …`, object-literal APIs such as koa's `context.js`) cannot be found by the exact-name retriever, only by BM25 and embeddings.
  - Hybrid fusion currently trails semantic-only retrieval on the benchmark (MRR 0.888 vs 0.933). RRF demotes chunks found by only one retriever. The lexical reranker did not help and is off by default. No cross-encoder reranker yet.
  - The benchmark is small (41 queries over two purpose-written repositories). Treat its numbers as regression signals, not general quality claims.

See `docs/` for the architecture, progress and decision log. `docs/CURRENT_STATE.md` is the development handoff (current commit, test results, open issues, next steps); `CLAUDE.md` holds the rules for AI coding sessions.
