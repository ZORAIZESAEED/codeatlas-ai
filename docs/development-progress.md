# Development progress

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

## Milestone 2 — Code analysis (branch `feature/milestone-2-code-analysis`)
- [x] Additive migration `0003_code_analysis` (`code_symbols`, `code_chunks`, analysis columns); existing projects preserved as `pending`
- [x] Python structural parsing with the standard-library `ast` (classes, functions, methods, decorators, docstrings)
- [x] Tree-sitter parsing for JavaScript, JSX, TypeScript and TSX (functions, arrow-function constants, classes, methods, interfaces, types, enums, namespaces, JSDoc)
- [x] Pluggable parser map with safe fallback (syntax errors, deep nesting, unsupported languages)
- [x] Symbol-aware chunking plus line-aware fallback (blank-line and Markdown-heading boundaries, small-piece merging, configurable limits, never splits a line)
- [x] Automatic analysis after import; `POST /analyze` (idempotent), `GET /symbols`, `GET /chunks`, analysis summary in `/overview`, parser info in `/files`
- [x] Frontend: analysis card with Analyze/Re-analyze, file outline (symbols and chunks) with line-range highlighting
- [x] Tests: parsers, chunking invariants, analysis API, migration of a pre-existing project
- [x] Technical review: `\n`-only line model (form feed / U+2028 / U+0085 fix), BOM-safe parsing, per-file failure isolation, structural parsing size limits, safe handling of projects deleted mid-analysis, regression tests (136 backend tests), headless-browser check of the Symbols/Chunks UI

## Next milestones
- [ ] Structural parsing for more languages (e.g. Go, Rust, Java via Tree-sitter grammars)
- [ ] Embeddings / vector indexing and search (M3)
- [ ] Source-grounded Q&A (M4)
- [ ] Possible follow-ups: background import jobs, authenticated/private GitHub imports, branch selection, browsing non-indexed text files
