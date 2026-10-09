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

## Next milestones
- [ ] Code parsing and symbol extraction (M2)
- [ ] Embeddings / vector indexing and search (M3)
- [ ] Source-grounded Q&A (M4)
- [ ] Possible follow-ups: background import jobs, authenticated/private GitHub imports, branch selection, browsing non-indexed text files
