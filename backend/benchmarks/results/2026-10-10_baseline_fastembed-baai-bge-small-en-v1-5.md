# Retrieval benchmark — 2026-10-10T03:29:16+00:00

- Queries: 41 (api_endpoint: 5, class_location: 4, configuration: 5, cross_file: 4, function_location: 5, keyword_identifier: 7, semantic: 11)
- Embedding: `fastembed` / `BAAI/bge-small-en-v1.5` (384d, input v1)
- Retrieval: RRF k=60, weights {'exact': 1.0, 'keyword': 1.0, 'semantic': 1.0}, pool 50, reranker features-v1
- Environment: Python 3.14.6, Windows-11-10.0.22621-SP0, 8 logical CPUs
- Repositories: shopapi (14 files, 48 chunks, indexed in 10.275 s), taskboard (16 files, 40 chunks, indexed in 9.028 s)

| Configuration | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | Precision@3 | Precision@5 | Hit@5 | Mean latency (ms) | p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| keyword | 0.384 | 0.652 | 0.750 | 0.829 | 0.600 | 0.325 | 0.234 | 0.829 | 15.3 | 23.9 |
| semantic | 0.744 | 0.878 | 0.927 | 0.963 | 0.933 | 0.431 | 0.283 | 1.000 | 41.6 | 49.3 |
| hybrid | 0.530 | 0.732 | 0.835 | 0.927 | 0.760 | 0.374 | 0.259 | 0.927 | 43.6 | 50.6 |
| hybrid+rerank | 0.518 | 0.726 | 0.787 | 0.915 | 0.743 | 0.366 | 0.249 | 0.878 | 607.0 | 972.9 |

## Recall@5 / MRR by category

| Category | keyword | semantic | hybrid | hybrid+rerank |
| --- | --- | --- | --- | --- |
| api_endpoint | 0.80 / 0.45 | 1.00 / 0.90 | 0.80 / 0.47 | 0.80 / 0.46 |
| class_location | 1.00 / 0.63 | 1.00 / 1.00 | 1.00 / 0.88 | 1.00 / 0.88 |
| configuration | 0.80 / 0.67 | 0.80 / 0.80 | 0.80 / 0.80 | 0.80 / 0.80 |
| cross_file | 0.50 / 0.29 | 0.75 / 1.00 | 0.62 / 0.75 | 0.62 / 0.75 |
| function_location | 0.60 / 0.39 | 1.00 / 1.00 | 0.80 / 0.57 | 0.60 / 0.56 |
| keyword_identifier | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| semantic | 0.61 / 0.58 | 0.91 / 0.89 | 0.80 / 0.77 | 0.70 / 0.72 |

## Queries missing a relevant result in the top 3 of any configuration

First relevant rank per configuration (— = not in the top 10).

- **py-fn-hash** (function_location) “Where are user passwords hashed before they are stored?”
  - expected: app/auth/security.py hash_password
  - ranks: keyword: —, semantic: 1, hybrid: 6, hybrid+rerank: 7
  - hybrid+rerank top 3: app/db/models.py:7-15 User; app/auth/security.py:21-26 verify_password; app/images/preprocess.py:1-9
- **py-sem-validate** (semantic) “Which code validates API request data?”
  - expected: app/schemas.py ProductCreate; app/schemas.py RegisterRequest
  - ranks: keyword: 7, semantic: 1, hybrid: 1, hybrid+rerank: 2
  - hybrid+rerank top 3: app/api/auth_routes.py:1-8; app/schemas.py:13-23 RegisterRequest; app/api/auth_routes.py:25-31 register
- **ts-fn-overdue** (function_location) “check whether a task is past its due date”
  - expected: client/src/utils/date.ts isOverdue
  - ranks: keyword: 8, semantic: 1, hybrid: 5, hybrid+rerank: 6
  - hybrid+rerank top 3: client/src/components/TaskList.tsx:5-5 Task; server/services/taskModel.ts:3-10 Task; client/src/utils/date.ts:3-5 formatDueDate
- **ts-cls-tasks** (class_location) “class that manages tasks in the database”
  - expected: server/services/taskService.ts TaskService
  - ranks: keyword: 5, semantic: 1, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: server/services/taskService.ts:3-24 TaskService; server/routes/tasks.ts:1-32; client/src/components/TaskList.tsx:7-30 TaskList
- **ts-api-delete** (api_endpoint) “endpoint to delete a task”
  - expected: server/routes/tasks.ts
  - ranks: keyword: 4, semantic: 2, hybrid: 4, hybrid+rerank: 5
  - hybrid+rerank top 3: client/src/components/TaskList.tsx:5-5 Task; server/services/taskModel.ts:3-10 Task; server/services/taskService.ts:3-24 TaskService
- **ts-api-signup** (api_endpoint) “route that registers a new account”
  - expected: server/routes/users.ts
  - ranks: keyword: —, semantic: 1, hybrid: 10, hybrid+rerank: 10
  - hybrid+rerank top 3: server/routes/tasks.ts:1-32; server/services/userModel.ts:9-14; server/db.ts:5-17 connectDatabase
- **ts-x-auth** (cross_file) “how are API requests authenticated between the client and the server”
  - expected: server/middleware/auth.ts requireAuth; client/src/api/client.ts apiFetch
  - ranks: keyword: 6, semantic: 1, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: client/src/api/client.ts:18-37 apiFetch; client/src/api/client.ts:1-1; client/src/api/client.ts:3-7 ApiError
- **ts-sem-validate** (semantic) “Which code validates API request data?”
  - expected: server/middleware/validate.ts validateBody
  - ranks: keyword: —, semantic: 1, hybrid: 4, hybrid+rerank: 6
  - hybrid+rerank top 3: client/src/api/client.ts:18-37 apiFetch; server/middleware/auth.ts:5-7 AuthedRequest; server/middleware/validate.ts:1-1
- **ts-sem-logout** (semantic) “log the user out when the server responds unauthorized”
  - expected: client/src/api/client.ts apiFetch; client/src/api/client.ts logout
  - ranks: keyword: —, semantic: 1, hybrid: —, hybrid+rerank: —
  - hybrid+rerank top 3: server/services/userModel.ts:3-7 User; server/routes/users.ts:14-34; server/middleware/auth.ts:5-7 AuthedRequest
- **ts-sem-done** (semantic) “mark a task as done”
  - expected: server/services/taskService.ts TaskService.completeTask; server/routes/tasks.ts
  - ranks: keyword: 5, semantic: 4, hybrid: 5, hybrid+rerank: 5
  - hybrid+rerank top 3: client/src/components/TaskList.tsx:5-5 Task; server/services/taskModel.ts:3-10 Task; client/src/components/TaskList.tsx:7-30 TaskList
