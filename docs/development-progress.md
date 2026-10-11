# Development progress

Milestone checklist (history). For the current branch, commit, test results, open issues and next steps, see [`CURRENT_STATE.md`](CURRENT_STATE.md).

## Milestone 0
- [x] FastAPI app with `/health`, `/ready`, and OpenAPI documentation
- [x] Pydantic environment configuration
- [x] SQLAlchemy model and Alembic migration
- [x] Next.js frontend foundation with backend status check
- [x] Docker files and local installation instructions
- [x] Initial backend automated tests

## Milestone 1 — Repository ingestion
- [x] `project_files` manifest table and project statistics columns (migration `0002_project_files`)
- [x] Secure ZIP extraction (traversal, absolute paths, symlinks, encryption, malformed archives, entry/size limits, per-file byte caps)
- [x] Public GitHub import (URL validation, metadata lookup, size-limited streaming download, rate-limit / 404 / network error handling)
- [x] ZIP upload endpoint (20 MB limit, `Content-Length` checked before parsing)
- [x] Language detection and filtering (12 languages; excluded folders, binaries, oversized and credential files)
- [x] Manifest: path, language, size, line count, SHA-256, project ID
- [x] Project list / detail / delete, file list, file content, overview endpoints
- [x] Transactional imports with workspace cleanup on failure
- [x] Developer dashboard: project sidebar, GitHub/ZIP import, overview statistics, collapsible file tree, syntax-highlighted code viewer with line numbers, responsive layout
- [x] Backend tests (65) with mocked GitHub HTTP; frontend type check and production build

## Milestone 2 — Code analysis (merged to `main` at `e9603a0`)
- [x] Additive migration `0003_code_analysis` (`code_symbols`, `code_chunks`, analysis columns); existing projects preserved as `pending`
- [x] Python structural parsing with the standard-library `ast` (classes, functions, methods, decorators, docstrings)
- [x] Tree-sitter parsing for JavaScript, JSX, TypeScript and TSX (functions, arrow-function constants, classes, methods, interfaces, types, enums, namespaces, JSDoc)
- [x] Pluggable parser map with safe fallback (syntax errors, deep nesting, unsupported languages)
- [x] Symbol-aware chunking plus line-aware fallback (blank-line and Markdown-heading boundaries, small-piece merging, configurable limits, never splits a line)
- [x] Automatic analysis after import; `POST /analyze` (idempotent), `GET /symbols`, `GET /chunks`, analysis summary in `/overview`, parser info in `/files`
- [x] Frontend: analysis card with Analyze/Re-analyze, file outline (symbols and chunks) with line-range highlighting
- [x] Tests: parsers, chunking invariants, analysis API, migration of a pre-existing project
- [x] Technical review: `\n`-only line model (form feed / U+2028 / U+0085 fix), BOM-safe parsing, per-file failure isolation, structural parsing size limits, safe handling of projects deleted mid-analysis, regression tests (136 backend tests), headless-browser check of the Symbols/Chunks UI

## Milestone 3 — Retrieval (merged to `main` at `29f737c`)
- [x] Milestone 2 fast-forward merged into `main` (`e9603a0`) after re-running its tests; Milestone 3 developed on `feature/milestone-3-retrieval` and fast-forward merged into `main` (`29f737c`)
- [x] Additive migration `0004_search_index` (`search_indexes`, `index_entries`, contentless FTS5 `chunk_fts`, `projects.analysis_revision`), tested up/down/up on a copy of the development database
- [x] Embedding provider interface; local fastembed provider (lazy, CPU, configurable model/batch/threads/query prefix); deterministic fake provider for tests
- [x] ChromaDB persistence: one collection per project and model, deterministic content-addressed record IDs, full chunk metadata, batched writes
- [x] Incremental indexing: unchanged chunks skipped, moved chunks reuse vectors, changed chunks re-embedded, removed chunks deleted; reconciliation of partial failures; stale detection after re-analysis or model change; real progress counters; chunk and time limits
- [x] Keyword search (exact identifiers/paths + FTS5 BM25 with identifier splitting, injection-safe), semantic search, weighted RRF hybrid with explicit keyword fallback, optional feature reranker, filters, source validation of every result
- [x] API: `POST /index`, `GET /index-status`, `POST /search`; project deletion removes index data
- [x] Frontend: search-index card with live progress; Search workspace (modes, filters, highlighted snippets, method badges, warnings); results open the code viewer at the cited lines
- [x] Retrieval benchmark (2 controlled repositories, 41 labelled queries, 7 categories) with recorded results for two models and a pre-fix baseline
- [x] Tests: 224 backend tests (136 existing + 88 new), including startup recovery after a killed run and the older-SQLite FTS path; Ruff; TypeScript check; production build
- [x] Live verification with `fastapi/full-stack-fastapi-template` and `koajs/koa` in isolated storage (indexing, three modes, citation checks, unchanged reindex, deletion, browser navigation)

## Milestone 4 — AI investigation and chat (branch `feature/milestone-4-ai-investigation`, not merged)
- [x] Branch created from `main` at `0ff0777` after the baseline checks (224 backend tests, `tsc`, `next build`)
- [x] LLM provider layer: typed interface (streaming, tools, usage, capabilities, cancellation, deadlines, typed errors), Ollama adapter, fake provider, `/api/llm/status`. Anthropic/OpenAI adapters are not enabled: they need their SDKs (approval pending)
- [x] Baseline RAG: evidence records bound to line hashes, token-budgeted context, fenced prompts, citation validation (invented, foreign, stale, missing, out-of-range), lexical support check
- [x] Additive migration `0005_conversations`, tested up/down/up on a copy of the development database (row counts unchanged); conversations, messages, citations and investigation runs with project isolation
- [x] Bounded investigation: six read-only tools, native and deterministic planners with hand-over on stalled native steps, all limits, de-duplication, trace
- [x] Streaming (SSE) with factual progress events; cancellation on Stop or disconnect closes the model connection; durable-on-completion persistence; startup recovery
- [x] Frontend AI Chat tab: conversation history (create/open/rename/delete), model status, mode selector, streamed answers with progress, Markdown, citation chips and Sources that open the code viewer at the lines, investigation activity, Stop, Copy
- [x] Answer benchmark (16 questions, 10 categories, deterministic checks), baseline vs. investigation
- [x] Quality review of the recorded false-call answer: static relationship verification (`rag/relations.py`), planner targets calls before imports and records why, prompt rule; regression tests for false, valid, cross-file and dynamic-dispatch claims
- [x] Tests: provider, RAG, citations, conversations, streaming, investigation and prompt injection (full-suite count in `docs/CURRENT_STATE.md`)
- [x] Live verification with `qwen2.5:3b` on isolated storage (Python, TypeScript and prompt-injection repositories; browser checks of streaming, citations, reopening, Stop)

## Next milestones
- [ ] Structural parsing for more languages (e.g. Go, Rust, Java via Tree-sitter grammars)
- [ ] Retrieval quality: path-aware weighting of tests/docs/generated files, fusion that does not penalize single-retriever hits, cross-encoder reranking, larger benchmark
- [ ] Background indexing jobs (cross-process lock) for large repositories
- [ ] M4 follow-ups: hosted providers (after SDK approval), better citation discipline for small models, LLM-as-judge evaluation alongside the deterministic checks, larger answer benchmark
- [ ] Possible follow-ups: background import jobs, authenticated/private GitHub imports, branch selection, browsing non-indexed text files
