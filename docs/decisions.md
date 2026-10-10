# Architecture decisions

1. **Separate backend and frontend.** Enables independent testing and deployments.
2. **SQLite at foundation stage.** Avoids an unnecessary database service for local development; switch to PostgreSQL when requirements grow.
3. **Alembic migration instead of runtime `create_all`.** Explicit, reviewable schema evolution.
4. **Factory-built FastAPI app.** Supports isolated tests and configurable application settings.
5. **No vector or LLM provider dependencies in M0.** No fake endpoints or costs until the features are built.

## Milestone 1
6. **GitHub via fixed hosts, not `git clone`.** Only `api.github.com` (metadata) and `codeload.github.com` (ZIP archive) are contacted, with validated owner/repo names in the path and redirects followed only within the API host. This prevents SSRF and avoids running Git (hooks, submodules, LFS filters).
7. **One extraction pipeline for both sources.** GitHub archives and uploads go through the same `extract_archive` code, so every security check applies to both.
8. **Extract only what is indexed.** Only supported-language, non-sensitive, ≤ 1 MB UTF-8 text files are written to disk. Excluded folders (e.g. `node_modules`) are never extracted, which bounds disk use and limits ZIP-bomb impact. Trade-off: file types outside the language registry are not browsable (see 17).
9. **Do not trust ZIP headers.** Each member is read with a hard byte cap and the running total is enforced, instead of relying on declared sizes or compression ratios.
10. **Reject whole archives on traversal or encryption; skip symlinks.** Path traversal signals a malicious archive, so it fails the import. Symlinks are common in real repositories, so they are skipped rather than rejected.
11. **Manifest in the database, files on disk.** `project_files` stores path, language, size, line count and SHA-256. Content is read from `<workspace>/<project_id>` and only for paths present in the manifest.
12. **Import transaction plus cleanup.** DB rows are committed only after extraction succeeds. Any failure rolls back and deletes the project folder. On delete, the DB commit happens first, then the folder is removed.
13. **Synchronous imports.** Imports run inside the request (in a worker thread). This is simpler for M1; background jobs can be introduced if imports become slow.
14. **Content-Length check before multipart parsing.** Oversized uploads are rejected before Starlette spools them to disk. Requests without `Content-Length` are refused (411).
15. **New dependencies (approved):** `python-multipart` (required for FastAPI uploads), `httpx` promoted from dev to runtime (streaming downloads with timeouts; mockable via `MockTransport`), and `highlight.js` core with only the needed grammars (small, no transitive dependencies).
16. **Tests run real Alembic migrations.** Project tests upgrade a temporary SQLite database to `head`, so the migration itself is exercised.
17. **Data-driven language registry (supersedes the hardcoded extension map).** Upcoming milestones need Dockerfiles, Go, text and config files, so detection is now driven by `LanguageSpec` entries in `languages.py`. Exact names are checked first, then globs (e.g. `Dockerfile.*`), then extensions. No database change was needed (`language` stays a free-text `String(30)`). Highlighting stays a frontend concern with plain-text fallback.
18. **Broader exclusions as the indexed set grows.** Indexing `.txt`, `.ini`, `.xml` etc. raises the risk of ingesting secrets and noise, so credential patterns (`secret(s).*`, `credential(s).*`, key/cert files, `.pgpass`, `.git-credentials`) and generated files (lockfiles, minified bundles, source maps) are skipped. Dependency/build directories now include `vendor`, `target`, `out`, `coverage` and `*.egg-info`. Trade-off: a hand-written source folder named `vendor`, `out` or `target` is also skipped. These stay excluded by default (approved). Repository-specific exclusion settings (e.g. per-project include/exclude overrides) may be added later. They are not implemented yet.

## Milestone 2
19. **Python `ast` + Tree-sitter.** Python uses the standard-library parser (exact, no dependency). JavaScript/JSX/TypeScript/TSX use Tree-sitter (`tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript`; requested). Tree-sitter is error-tolerant, so files with syntax errors still yield symbols. Both only build syntax trees; nothing is executed.
20. **Parser map keyed by registry language name.** `PARSERS` in `parsing.py` mirrors the language registry: a new structural parser is one entry, and everything else falls back to line chunking. `parse_source` never raises, so one bad file cannot fail a project.
21. **Store chunk boundaries, not chunk text.** `code_chunks` holds line ranges, character count and SHA-256. Text is sliced from the workspace file on demand, which avoids duplicating every repository in SQLite. The hash lets Milestone 3 detect changed chunks.
22. **Chunking never splits a line.** Over-long single lines (e.g. minified code) become their own chunk, even above the character limit.
23. **Symbol ranges include decorators and the leading comment block.** Docs and comments stay in the same chunk as the code they describe. Trailing comments of the previous statement are not attached.
24. **Small-piece merging and punctuation folding.** Adjacent fallback pieces under a quarter of `CHUNK_MAX_LINES` merge while they fit, so short Markdown sections don't become one-line chunks. Chunks without alphanumeric content (e.g. a closing `}`) join the previous chunk.
25. **Analysis is separate from import.** It runs right after a successful import, in its own transaction. Failures mark the project `failed` without undoing the import or deleting earlier results. Re-analysis replaces symbols and chunks atomically, so it is idempotent.
26. **Existing projects are not analyzed inside the migration.** Migrations stay schema-only and fast. Pre-existing projects become `pending` and are analyzed on demand via `POST /analyze` or the UI button.
27. **One line model everywhere: lines end at `\n`.** `ast`, Tree-sitter, `line_count`, the chunker and the UI gutter all count `\n` only. Python's `str.splitlines()` (used at first) also breaks on form feeds, U+2028 and U+0085, which made chunk ranges drift from symbol ranges. A UTF-8 BOM is stripped before parsing only; stored content is unchanged. Known exception: classic-Mac files with lone `\r` line endings count as one long line.
28. **Per-file failure isolation; analysis status is separate from import status.** Parsing and chunking errors (expected or not) degrade only the affected file to line chunking and are reported as parse issues. Project-level failures roll back, keep earlier results and set `analysis_status=failed`, while the import (`status=ready`) and its files stay usable.
29. **Bounded structural parsing.** `PARSE_MAX_FILE_BYTES` (512 KB) and `PARSE_MAX_TOTAL_BYTES` (50 MB per project) cap parser work during the synchronous post-import analysis. Line chunking is linear and always applied, so every file still gets chunks.

## Milestone 3
30. **Embeddings: fastembed (ONNX Runtime) with `BAAI/bge-small-en-v1.5` as the default.** Evaluated options:
    - `sentence-transformers` + PyTorch: hundreds of MB of torch/scipy/scikit-learn for the same models.
    - fastembed: ONNX, reuses the `onnxruntime` that ChromaDB already installs.
    - ChromaDB's bundled MiniLM.

    fastembed was chosen for Windows/CPU friendliness and size. The model was chosen by measurement on the retrieval benchmark (decision 44): `bge-small-en-v1.5` (384 d, 512-token limit, ~67 MB) reached semantic MRR 0.933, against 0.844 for `all-MiniLM-L6-v2`, at roughly twice the CPU cost. The code-aware `jinaai/jina-embeddings-v2-base-code` (768 d, ~640 MB) is not the default because of its size and CPU cost; it can be selected via `EMBEDDING_MODEL`. Models download lazily on first indexing or semantic query into `EMBEDDING_CACHE_DIR`, never at import or startup. Embedding stays local; no paid API is required.
31. **Batch size 4 by default.** The default model is int8-quantized. On the development CPU (Intel Skylake, no VNNI), batches of 32 long code chunks were about 3.5× slower than batches of 4, because of padding and memory traffic. `EMBEDDING_BATCH_SIZE` is configurable.
32. **Reuse Milestone 2 chunks; never re-chunk.** Indexing reads `code_chunks`, slices the text from the workspace and verifies each chunk's SHA-256 before use.
    - The embedding input adds a short header (path, language, symbol, symbol kind, split name words).
    - It is capped at `EMBEDDING_MAX_INPUT_CHARS` (2000), so the header always falls inside the model's 512-token window. The keyword index sees the full chunk.
    - The format is versioned (`EMBEDDING_INPUT_VERSION = "v1"`). A change marks indexes stale and forces re-embedding.
33. **Content-addressed index records.** `code_chunks.id` changes on every re-analysis, so it cannot identify indexed content.
    - `record_id` = sha256(project, path, lines, content hash, kind, symbol, symbol kind, language). It is the deterministic ChromaDB id.
    - `embed_hash` = sha256(input version, embedding text). It excludes line numbers, so a chunk that only moved reuses its stored vector instead of being re-embedded.
34. **One ChromaDB collection per project, provider, model, dimension and input version.**
    - Project isolation is structural: a query only ever opens one project's collection.
    - Vectors from different models or dimensions can never be mixed.
    - Queries are embedded with the model recorded on the index, not the configured one. A configuration change therefore makes the index *stale* rather than wrong.
    - Vectors are stored only in ChromaDB (cosine HNSW), never duplicated in SQLite. Chroma telemetry is disabled.
35. **SQLite is the source of truth; explicit write ordering instead of a cross-database transaction.** SQLite and ChromaDB cannot commit atomically. Each run follows this order:
    1. Reconcile ChromaDB ids against `index_entries`.
    2. Upsert new vectors (idempotent, content-addressed).
    3. In one SQLite transaction, swap the manifest and FTS rows and mark the index `ready`.
    4. Delete obsolete vectors.

    A failure before the SQLite commit keeps the previous manifest and its vectors and marks the index `failed`; the next run reuses the vectors the failed run wrote. A failure while deleting leaves unreferenced vectors. Search ignores them, because hits are mapped through the manifest, and the next run removes them. A deleted project's collections are removed after the SQLite delete commits. Orphaned collections are swept on the next indexing run.
36. **"Stale" is derived, not stored.** An index is stale when `projects.analysis_revision` (incremented by every successful analysis) differs from the revision it was built from, or when the configured model or input version differs. Because it is computed, no code path can forget to set it. Search on a stale or failed index still runs over the previous index and adds a warning.
37. **Keyword search: contentless SQLite FTS5.**
    - `chunk_fts` stores tokens, not source (`contentless_delete=1`, needs SQLite ≥ 3.43; 3.50 here). On older SQLite (e.g. some Debian-based Docker images) the migration creates a regular FTS5 table instead. It behaves the same but also stores the indexed text. Both paths are tested.
    - `_` is a token character, so `create_access_token` matches exactly. Split sub-words (`create access token`) are indexed alongside identifiers, so `access token` finds `createAccessToken`.
    - `index_entries.id` doubles as the FTS rowid and uses `AUTOINCREMENT`, so an id is never reused by an unrelated chunk.
    - Queries are reduced to word tokens and each token is double-quoted, so FTS operators cannot be injected. All values are bound parameters.
    - A separate *exact* retriever matches identifiers and paths named in the query against symbol names and file paths.
38. **Exact matching is case-sensitive for plain words.** The first benchmark run showed the exact retriever matching everyday words to symbols case-insensitively ("user" → class `User`, "task" → `Task`). That put wrong chunks at the top of natural-language queries. Identifier-shaped tokens (`snake_case`, `camelCase`, dotted names, paths) still match case-insensitively. Plain words match a symbol only with identical case, so "login" still finds `login()`. Hybrid MRR rose from 0.760 to 0.815.
39. **Weighted Reciprocal Rank Fusion with a query-aware keyword weight.** RRF (k = 60) fuses ranks only, so BM25 scores and cosine distances never need a common scale.
    - BM25 keeps weight 1.0 when the query names code. It drops to 0.25 for plain natural-language questions, where its matches on common words outvote the semantic top hit.
    - 0.25 was chosen on the `shopapi` benchmark repository and checked on the held-out `taskboard` repository, where hybrid MRR rose from 0.703 to 0.776.
    - Even so, semantic-only retrieval scores higher than hybrid on this benchmark (MRR 0.933 vs 0.888), especially on `taskboard`. The cause is a known RRF weakness: a chunk found by only one retriever loses to chunks found mediocrely by both.
    - Hybrid stays the default because exact identifier/path lookups and keyword fallback matter on real repositories. The result is recorded, not tuned away.
    - Weights are configurable (`RRF_*`), and every search response returns the weights it used.
40. **Reranking is opt-in.** A deterministic feature reranker (exact symbol/path, identifier overlap and term coverage, added to the normalized fusion score) sits behind a `Reranker` protocol, so a cross-encoder can be added later. It lowered MRR in every benchmark configuration, so `SEARCH_RERANK_DEFAULT=false`; a request can pass `"rerank": true`. Scores are ranking scores and are never presented as confidence.
41. **Hybrid falls back explicitly; semantic fails explicitly.** If embeddings are unavailable, `mode=semantic` returns 503. `mode=hybrid` returns keyword results with `mode_used="keyword"`, a `fallback_reason` and a warning, and the UI shows the fallback. A response never claims hybrid retrieval when only keyword retrieval ran.
42. **Source validation at query time.** Every candidate is re-read from the workspace through the manifest-checked reader. Its current text must hash to the indexed content hash, otherwise it is dropped and counted, so citations always point at existing lines. Filters (language, file path, directory, symbol type) only compare against manifest values: `..` and control characters are rejected and LIKE wildcards are escaped.
43. **Synchronous indexing with persisted real progress.** `POST /index` runs in the request's worker thread, one run per project at a time (an in-process guard returns 409). Progress counters are committed per batch, and the UI polls `GET /index-status` while the request is open. There are no estimates or fake percentages.
    - Resource limits: `INDEX_MAX_CHUNKS` (50,000) and `INDEX_TIMEOUT_SECONDS` (1,800).
    - **Interruption.** A client disconnect does not cancel the run: the synchronous endpoint keeps working in its thread and records the result (verified live: the client gave up after 5 s and the run finished `ready`). If the server stops mid-run, the row is left `indexing`. At the next startup the app marks every such row `failed` with an "interrupted" error, because indexing only runs inside the API process. The previous complete index is unchanged and searchable. The next run reuses vectors written before the stop: verified live by killing the server at 32/248 chunks, after which the next run embedded 216 and reused 32. As a backstop, an `indexing` row older than `INDEX_TIMEOUT_SECONDS + 120 s` is reported as interrupted.
    - **Reporting.** A failed run reports its error. If an earlier complete index exists, the response also reports whether that index is stale. Search on it adds a warning naming both conditions. A failed first run makes search return 409 with the failure reason. The manifest swap is a single transaction, so search never sees a partially written index.
    - No job queue was added. A multi-worker deployment would need a cross-process lock, and the startup recovery would then wrongly fail another worker's live run. Running several workers is not supported.
44. **Benchmark with controlled repositories and complete labels.** `backend/benchmarks/` holds two small purpose-written repositories (a Python API and a TypeScript Express + React app) and 41 labelled queries in 7 categories.
    - Labels name relevant chunks by path plus symbol and/or a substring. They are resolved against the real index, and a label that matches nothing aborts the run.
    - Labels are treated as complete for these repositories, so Precision@K is meaningful.
    - Every configuration uses the same labels. Results are written to `backend/benchmarks/results/` with date, model, configuration, environment and latency.
