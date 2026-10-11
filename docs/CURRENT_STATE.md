# CodeAtlas — current state (development handoff)

> **Authoritative handoff for the next development session.** Read this first, then **verify** every claim below against Git and the source before relying on it (see `CLAUDE.md` → Session protocol). Update this file before finishing substantial work. Record only results that were actually observed.

**Last updated:** 2026-10-11 (after the Milestone 4 quality review). Milestone 4 is committed on `feature/milestone-4-ai-investigation` as `feat: implement AI codebase investigation and grounded RAG chat` (the commit that added this text; find it with `git log --oneline -1` on the branch). It is **not merged**: `main` is unchanged at `0ff0777`.

## Verify this file in 30 seconds
```powershell
git status
git branch -vv
git log --oneline -5
dir backend\migrations\versions
.\.venv\Scripts\python -c "import sqlite3; print(sqlite3.connect('backend/data/codeatlas.db').execute('select version_num from alembic_version').fetchone())"
```
Expected as of the date above:
- Branch `feature/milestone-4-ai-investigation`: HEAD is the Milestone 4 commit, whose parent is `0ff0777` (= `main`); clean working tree.
- Migrations `0001`–`0005`.
- Local dev DB still at `0004_search_index`. `0005` has not been applied to it: run `alembic upgrade head` to use chat.

## Repository
| Item | Value (2026-10-11) |
| --- | --- |
| Location | `C:\CodeAtlas_Milestone_0\codeatlas-m0` (virtualenv `.venv` at the root; Python 3.14.6) |
| Current branch | `feature/milestone-4-ai-investigation` (created from `main` at `0ff0777`) |
| `main` HEAD | `0ff0777` docs: establish persistent Claude Code project context (on top of `29f737c` Milestone 3) |
| Remote | none configured |
| Alembic head | `0005_conversations` (chain `0001` → `0002` → `0003` → `0004_search_index` → `0005_conversations`) |
| Local dev DB | `backend/data/codeatlas.db` at `0004_search_index` (not upgraded; `0005` was tested up/down/up on a copy, with row counts unchanged) |
| Local model | Ollama 0.40.2 with `qwen2.5:3b`, `qwen2.5:1.5b` and `qwen2.5-coder:7b` installed (not part of the repository) |

## Completed milestones
- **M0–M3:** foundation, ingestion/browsing, code analysis, retrieval (see `docs/development-progress.md`).
- **M4 – AI investigation and chat (committed on the feature branch, not merged):**
  - Ollama provider plus a fake provider; `/api/llm/status`.
  - Baseline RAG with token-budgeted, fenced evidence and validated citations.
  - Bounded investigation with six read-only tools and native/deterministic planners.
  - Persistent conversations (migration `0005`).
  - SSE streaming with cancellation.
  - AI Chat tab.
  - Answer benchmark.
  - Details: `README.md` → "AI chat", `docs/architecture.md` → Milestone 4, `docs/decisions.md` 45–56.

## Quality review: false relationship claims (2026-10-11)
- **Failure:** the recorded benchmark answer (`py-api-products`, investigation) said "`create_product` also calls `save_product_image` [E5]". This is false; only `upload_product_image` calls it. The citation itself was valid.
- **Root cause** (reproduced from the recorded data):
  - the deterministic planner followed names imported by the module's import block as if they were calls, and fetched `save_product_image`;
  - evidence blocks did not say why they were retrieved;
  - the lexical support check flagged the sentence (0.667 unsupported) only as a metric.
- **Fix:**
  - `backend/app/services/rag/relations.py` statically verifies explicit "A calls / imports / returns B" claims with the existing `ast` / Tree-sitter parsers and symbol table. Verdicts are verified, inferred, unverified and unsupported. Text is annotated, never deleted.
  - The planner weights imports at half of calls and records the reason in the evidence header.
  - A prompt rule says an import or co-located code is not evidence of a call.
  - Checks are stored in the run trace (`relationship_check`), shown in the activity panel, and reported as benchmark metrics (not pass criteria).
- **Offline replay on all 32 recorded answers (no model run):** 4 explicit claims found. The false claim is now `unsupported` and labelled "**Unsupported claim:** … (Not supported by the source: no call to `save_product_image` appears in `create_product` (app/api/product_routes.py:16-22).)". The 3 true claims are `verified` and unchanged. The other 28 answers are unchanged.
- **Browser check (isolated 8001/3001, replayed answer in the scratch DB):** the bold label, the note and the activity entry render.
- **Not re-run:** the 16-question real-model benchmark (not authorized). The recorded scores above are from before this change.
- **Limits:**
  - Only explicit sentences with code-name subjects are checked.
  - Only direct calls in A's own definition count.
  - Dynamic dispatch, callbacks, `getattr`/computed calls, unknown symbols and languages without a parser are reported as inferred or unverified, never as false.
  - Claims about behaviour or data flow are not verified.

## Verified test results (2026-10-11, working tree of the feature branch)
- Backend: `pytest -q`: **308 passed** (224 existing + 84 new, including 12 relationship tests), 2 known deprecation warnings, 4 min 38 s; re-run on the final tree just before the commit: 308 passed (4 min 12 s), Ruff clean, `tsc` passed, `next build` compiled successfully
- Earlier in the day, before the quality review: 296 passed
- Ruff: `ruff check app tests`: all checks passed
- Frontend: `npm run lint` (tsc) passed; `npm run build` compiled successfully (built with the default API URL)
- Baseline before the work: 224 passed, tsc and build passed on `main` at `0ff0777`

## Live verification (isolated storage, `qwen2.5:3b`, CPU)
Setup: backend on port 8001 with its own DB, workspaces and Chroma under the session scratchpad; production frontend on port 3001. The user's servers on 8000/3000 were not touched.
- **Repositories:** `shopapi` (Python), `taskboard` (TypeScript) and `billing` (prompt-injection comments, strings and Markdown), all indexed with `bge-small`.
- **Model speed:** `qwen2.5-coder:7b` processes prompts at 4.9 tokens/s and generates at 2.0 tokens/s. An 8K-context baseline answer timed out after 300 s and was stored as failed. `qwen2.5:3b` processes 16.5 tokens/s and generates 3.7 tokens/s.
- **Answers seen:**
  - Baseline (JWT create/validate): 74 s, 4 verified citations, claims checked against the source.
  - Investigation (login flow): 157 s, 3 tool calls.
  - Follow-up ("where is that token validated?"): correct (`decode_access_token`).
  - Unanswerable (push notifications): an honest refusal.
  - Injection repository: answered correctly, ignored the injected instructions.
  - TypeScript task-creation flow: correct, but only the route was cited.
- **Browser (3001):** the model status shows; saved conversations reopen; an investigation streamed real progress (search → planning → `find_symbol` ×4 → answer, 147.6 s, 2 verified citations); clicking a citation opened `app/auth/security.py` with lines 33–44 highlighted; Stop produced a `cancelled` row with empty content and no citations.
- **Isolation and availability (live API):**
  - Another project's conversation returns 404.
  - Another project's file in `file_paths` returns 422, a traversal path returns 422, and a cross-project file read returns 404.
  - With Ollama unreachable, status explains how to start it, chat returns 503 before streaming, and nothing is stored.
- **Found and fixed during live testing:**
  - The 3B model cited paths instead of IDs (prompt strengthened).
  - The native planner made redundant searches (hand-over to the deterministic planner).
  - Cancellation took 59 s during prompt processing; it now takes effect at once (3.0 s at the moment of cancel).
  - A UI race showed a stopped answer as "failed".
  - Invalid selected files were stored before being rejected.

## Answer benchmark (`backend/benchmarks/results/2026-10-11_answers_ollama-qwen2-5-3b.*`)
| Mode | Pass | Citation recall | Mention recall | Citation validity | Unsupported (lexical) | Refusals | Forbidden | Tools/q | LLM req/q | Median s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 0.688 | 0.762 | 0.893 | 1.0 | 0.40 | 1.0 | 0 | 0 | 1 | 45.4 |
| investigation | 0.938 | 1.0 | 0.964 | 1.0 | 0.367 | 1.0 | 0 | 3.5 | 2.31 | 75.1 |

16 questions, one run per mode, total 2,146 s. Deterministic checks only, so these are proxies.

## Unresolved issues and known limitations
1. **Not merged:** Milestone 4 is committed on the feature branch only. Merging into `main` needs the user's approval. Migration `0005` is not applied to the dev DB.
2. **Hosted providers not enabled:** Anthropic and OpenAI need their official SDKs, which need approval to add.
3. **Claims are only partly verified.** Citation validity is checked. Explicit call/import/return claims are now verified statically (see the quality review above). Other claims (what code does, data flow) rely on the lexical support estimate only, which was about 0.4 unsupported in the recorded run. Relationship sentences with pronoun subjects ("it calls …") are not checked.
4. **CPU latency:** 40–150 s per answer with the 3B model; the 7B model is impractical on this machine without a GPU.
5. **Retrieval issues carry over from M3:** hybrid trails semantic on the retrieval benchmark, there is no path weighting, and there are symbol gaps for CommonJS. Synchronous, single-process indexing and chat.
6. **Small benchmarks:** the answer benchmark is small (16 questions) and was run once per mode.

## Exact next steps
1. Optionally re-run the 16-question real-model benchmark (about 36 min on this CPU; needs the user's go-ahead) to measure the relationship check and the planner change on fresh answers.
2. The user decides whether to merge `feature/milestone-4-ai-investigation` into `main`. Then apply `0005` to the dev DB: `cd backend; ..\.venv\Scripts\alembic upgrade head` (back up `data\codeatlas.db` first).
3. Decide on hosted providers (SDK dependencies, off-machine disclosure).
4. Quality work, measured with the answer benchmark: a claim-level support check (and/or a documented LLM judge), a larger and repeated benchmark, and path-aware retrieval weighting.

## Where things are recorded
- Engineering facts and rationale: `docs/architecture.md`, `docs/decisions.md`, `docs/development-progress.md`, `README.md`.
- This handoff: `docs/CURRENT_STATE.md`. Session rules: `CLAUDE.md`.
- Personal research notes and product ideas: **not in this repository's docs.**
