# Retrieval benchmark — 2026-10-10T03:50:07+00:00

- Queries: 41 (api_endpoint: 5, class_location: 4, configuration: 5, cross_file: 4, function_location: 5, keyword_identifier: 7, semantic: 11)
- Embedding: `fastembed` / `BAAI/bge-small-en-v1.5` (384d, input v1)
- Retrieval: RRF k=60, weights {'exact': 1.0, 'keyword_identifier_query': 1.0, 'keyword_natural_query': 0.25, 'semantic': 1.0}, pool 50, reranker features-v1
- Environment: Python 3.14.6, Windows-11-10.0.22621-SP0, 8 logical CPUs
- Repositories: shopapi (14 files, 48 chunks, indexed in 12.038 s), taskboard (16 files, 40 chunks, indexed in 6.314 s)

| Configuration | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | Precision@3 | Precision@5 | Hit@5 | Mean latency (ms) | p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| keyword | 0.457 | 0.689 | 0.750 | 0.829 | 0.646 | 0.342 | 0.234 | 0.829 | 16.7 | 27.1 |
| semantic | 0.744 | 0.878 | 0.927 | 0.963 | 0.933 | 0.431 | 0.283 | 1.000 | 45.7 | 52.5 |
| hybrid | 0.695 | 0.842 | 0.878 | 0.915 | 0.888 | 0.415 | 0.273 | 0.951 | 49.6 | 55.7 |
| hybrid+rerank | 0.640 | 0.805 | 0.866 | 0.927 | 0.837 | 0.398 | 0.263 | 0.951 | 51.9 | 59.5 |

## Recall@5 / MRR by repository

| Repository | keyword | semantic | hybrid | hybrid+rerank |
| --- | --- | --- | --- | --- |
| shopapi | 0.81 / 0.71 | 0.90 / 0.94 | 0.90 / 0.96 | 0.88 / 0.92 |
| taskboard | 0.66 / 0.54 | 0.97 / 0.92 | 0.84 / 0.78 | 0.84 / 0.71 |

## Recall@5 / MRR by category

| Category | keyword | semantic | hybrid | hybrid+rerank |
| --- | --- | --- | --- | --- |
| api_endpoint | 0.80 / 0.70 | 1.00 / 0.90 | 0.80 / 0.73 | 0.80 / 0.68 |
| class_location | 1.00 / 0.63 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.88 |
| configuration | 0.80 / 0.67 | 0.80 / 0.80 | 0.80 / 0.80 | 0.80 / 0.80 |
| cross_file | 0.50 / 0.33 | 0.75 / 1.00 | 0.75 / 1.00 | 0.75 / 0.88 |
| function_location | 0.60 / 0.49 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.87 |
| keyword_identifier | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| semantic | 0.61 / 0.58 | 0.91 / 0.89 | 0.82 / 0.80 | 0.77 / 0.78 |

## Queries missing a relevant result in the top 3 of any configuration

First relevant rank per configuration (— = not in the top 10).

- **py-fn-hash** (function_location) “Where are user passwords hashed before they are stored?”
  - expected: app/auth/security.py hash_password
  - ranks: keyword: —, semantic: 1, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: app/auth/security.py:14-18 hash_password; app/auth/security.py:21-26 verify_password; app/db/models.py:7-15 User
- **py-sem-validate** (semantic) “Which code validates API request data?”
  - expected: app/schemas.py ProductCreate; app/schemas.py RegisterRequest
  - ranks: keyword: 7, semantic: 1, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: app/schemas.py:13-23 RegisterRequest; app/api/auth_routes.py:1-8; app/api/auth_routes.py:25-31 register
- **ts-fn-overdue** (function_location) “check whether a task is past its due date”
  - expected: client/src/utils/date.ts isOverdue
  - ranks: keyword: 8, semantic: 1, hybrid: 1, hybrid+rerank: 3
  - hybrid+rerank top 3: client/src/utils/date.ts:3-5 formatDueDate; client/src/components/TaskList.tsx:5-5 Task; client/src/utils/date.ts:7-12 isOverdue
- **ts-cls-tasks** (class_location) “class that manages tasks in the database”
  - expected: server/services/taskService.ts TaskService
  - ranks: keyword: 5, semantic: 1, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: server/services/taskService.ts:3-24 TaskService; client/src/components/TaskList.tsx:7-30 TaskList; server/routes/tasks.ts:1-32
- **ts-api-delete** (api_endpoint) “endpoint to delete a task”
  - expected: server/routes/tasks.ts
  - ranks: keyword: 2, semantic: 2, hybrid: 2, hybrid+rerank: 4
  - hybrid+rerank top 3: server/services/taskService.ts:3-24 TaskService; client/src/components/TaskList.tsx:5-5 Task; client/src/components/TaskList.tsx:7-30 TaskList
- **ts-api-signup** (api_endpoint) “route that registers a new account”
  - expected: server/routes/users.ts
  - ranks: keyword: —, semantic: 1, hybrid: 6, hybrid+rerank: 6
  - hybrid+rerank top 3: server/routes/tasks.ts:1-32; server/services/userModel.ts:9-14; server/db.ts:5-17 connectDatabase
- **ts-x-auth** (cross_file) “how are API requests authenticated between the client and the server”
  - expected: server/middleware/auth.ts requireAuth; client/src/api/client.ts apiFetch
  - ranks: keyword: 6, semantic: 1, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: client/src/api/client.ts:18-37 apiFetch; server/index.ts:1-20; client/src/api/client.ts:1-1
- **ts-sem-validate** (semantic) “Which code validates API request data?”
  - expected: server/middleware/validate.ts validateBody
  - ranks: keyword: —, semantic: 1, hybrid: 2, hybrid+rerank: 3
  - hybrid+rerank top 3: client/src/api/client.ts:18-37 apiFetch; server/middleware/validate.ts:1-1; server/middleware/validate.ts:6-23 validateBody
- **ts-sem-logout** (semantic) “log the user out when the server responds unauthorized”
  - expected: client/src/api/client.ts apiFetch; client/src/api/client.ts logout
  - ranks: keyword: —, semantic: 1, hybrid: —, hybrid+rerank: —
  - hybrid+rerank top 3: server/routes/users.ts:14-34; server/services/userModel.ts:3-7 User; server/middleware/auth.ts:9-22 requireAuth
- **ts-sem-done** (semantic) “mark a task as done”
  - expected: server/services/taskService.ts TaskService.completeTask; server/routes/tasks.ts
  - ranks: keyword: 5, semantic: 4, hybrid: 4, hybrid+rerank: 4
  - hybrid+rerank top 3: client/src/components/TaskList.tsx:7-30 TaskList; client/src/components/TaskList.tsx:5-5 Task; server/services/taskModel.ts:3-10 Task
