# Architecture decisions

1. **Separate backend and frontend.** Enables independent testing and deployments.
2. **SQLite at foundation stage.** Avoids an unnecessary database service for local development; switch to PostgreSQL when requirements grow.
3. **Alembic migration instead of runtime `create_all`.** Explicit, reviewable schema evolution.
4. **Factory-built FastAPI app.** Supports isolated tests and configurable application settings.
5. **No vector or LLM provider dependencies in M0.** No fake endpoints or costs until the features are built.

## Milestone 1
6. **GitHub via fixed hosts, not `git clone`.** Only `api.github.com` (metadata) and `codeload.github.com` (ZIP archive) are contacted, with validated owner/repo names in the path and redirects followed only within the API host. This prevents SSRF and avoids running Git (hooks, submodules, LFS filters).
7. **One extraction pipeline for both sources.** GitHub archives and uploads go through the same `extract_archive` code, so every security check applies to both.
8. **Extract only what is indexed.** Only supported-language, non-sensitive, ≤ 1 MB UTF-8 text files are written to disk. Excluded folders (e.g. `node_modules`) are never extracted, which bounds disk use and limits ZIP-bomb impact. Trade-off: other text files (`Dockerfile`, `.txt`, `.go`) are not browsable yet.
9. **Do not trust ZIP headers.** Each member is read with a hard byte cap and the running total is enforced, instead of relying on declared sizes or compression ratios.
10. **Reject whole archives on traversal or encryption; skip symlinks.** Path traversal signals a malicious archive, so it fails the import. Symlinks are common in real repositories, so they are skipped rather than rejected.
11. **Manifest in the database, files on disk.** `project_files` stores path, language, size, line count and SHA-256. Content is read from `<workspace>/<project_id>` and only for paths present in the manifest.
12. **Import transaction plus cleanup.** DB rows are committed only after extraction succeeds. Any failure rolls back and deletes the project folder. On delete, the DB commit happens first, then the folder is removed.
13. **Synchronous imports.** Imports run inside the request (in a worker thread). This is simpler for M1; background jobs can be introduced if imports become slow.
14. **Content-Length check before multipart parsing.** Oversized uploads are rejected before Starlette spools them to disk. Requests without `Content-Length` are refused (411).
15. **New dependencies (approved):** `python-multipart` (required for FastAPI uploads), `httpx` promoted from dev to runtime (streaming downloads with timeouts; mockable via `MockTransport`), and `highlight.js` core with only the needed grammars (small, no transitive dependencies).
16. **Tests run real Alembic migrations.** Project tests upgrade a temporary SQLite database to `head`, so the migration itself is exercised.
