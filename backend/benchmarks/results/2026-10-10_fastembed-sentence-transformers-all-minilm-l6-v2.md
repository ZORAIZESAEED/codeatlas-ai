# Retrieval benchmark — 2026-10-10T03:50:59+00:00

- Queries: 41 (api_endpoint: 5, class_location: 4, configuration: 5, cross_file: 4, function_location: 5, keyword_identifier: 7, semantic: 11)
- Embedding: `fastembed` / `sentence-transformers/all-MiniLM-L6-v2` (384d, input v1)
- Retrieval: RRF k=60, weights {'exact': 1.0, 'keyword_identifier_query': 1.0, 'keyword_natural_query': 0.25, 'semantic': 1.0}, pool 50, reranker features-v1
- Environment: Python 3.14.6, Windows-11-10.0.22621-SP0, 8 logical CPUs
- Repositories: shopapi (14 files, 48 chunks, indexed in 13.281 s), taskboard (16 files, 40 chunks, indexed in 3.714 s)

| Configuration | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | Precision@3 | Precision@5 | Hit@5 | Mean latency (ms) | p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| keyword | 0.457 | 0.689 | 0.750 | 0.829 | 0.646 | 0.342 | 0.234 | 0.829 | 30.9 | 55.6 |
| semantic | 0.622 | 0.866 | 0.915 | 0.957 | 0.844 | 0.423 | 0.283 | 1.000 | 56.3 | 68.0 |
| hybrid | 0.598 | 0.854 | 0.902 | 0.939 | 0.822 | 0.431 | 0.278 | 0.976 | 66.0 | 95.8 |
| hybrid+rerank | 0.622 | 0.829 | 0.860 | 0.939 | 0.831 | 0.423 | 0.263 | 0.951 | 70.1 | 87.2 |

## Recall@5 / MRR by repository

| Repository | keyword | semantic | hybrid | hybrid+rerank |
| --- | --- | --- | --- | --- |
| shopapi | 0.81 / 0.71 | 0.90 / 0.85 | 0.90 / 0.89 | 0.87 / 0.89 |
| taskboard | 0.66 / 0.54 | 0.94 / 0.83 | 0.91 / 0.72 | 0.84 / 0.73 |

## Recall@5 / MRR by category

| Category | keyword | semantic | hybrid | hybrid+rerank |
| --- | --- | --- | --- | --- |
| api_endpoint | 0.80 / 0.70 | 1.00 / 0.90 | 1.00 / 0.74 | 0.80 / 0.73 |
| class_location | 1.00 / 0.63 | 1.00 / 0.88 | 1.00 / 1.00 | 1.00 / 1.00 |
| configuration | 0.80 / 0.67 | 0.80 / 0.63 | 0.80 / 0.67 | 0.80 / 0.70 |
| cross_file | 0.50 / 0.33 | 0.75 / 1.00 | 0.75 / 1.00 | 0.75 / 1.00 |
| function_location | 0.60 / 0.49 | 1.00 / 0.77 | 1.00 / 0.77 | 1.00 / 0.75 |
| keyword_identifier | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| semantic | 0.61 / 0.58 | 0.86 / 0.78 | 0.82 / 0.71 | 0.75 / 0.74 |

## Queries missing a relevant result in the top 3 of any configuration

First relevant rank per configuration (— = not in the top 10).

- **py-fn-hash** (function_location) “Where are user passwords hashed before they are stored?”
  - expected: app/auth/security.py hash_password
  - ranks: keyword: —, semantic: 1, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: app/auth/security.py:14-18 hash_password; app/auth/service.py:1-2; app/auth/security.py:21-26 verify_password
- **py-sem-validate** (semantic) “Which code validates API request data?”
  - expected: app/schemas.py ProductCreate; app/schemas.py RegisterRequest
  - ranks: keyword: 7, semantic: 4, hybrid: 3, hybrid+rerank: 3
  - hybrid+rerank top 3: app/api/auth_routes.py:1-8; app/api/auth_routes.py:25-31 register; app/schemas.py:13-23 RegisterRequest
- **ts-fn-overdue** (function_location) “check whether a task is past its due date”
  - expected: client/src/utils/date.ts isOverdue
  - ranks: keyword: 8, semantic: 3, hybrid: 3, hybrid+rerank: 4
  - hybrid+rerank top 3: client/src/components/TaskList.tsx:5-5 Task; server/services/taskModel.ts:3-10 Task; server/services/taskModel.ts:12-20
- **ts-cls-tasks** (class_location) “class that manages tasks in the database”
  - expected: server/services/taskService.ts TaskService
  - ranks: keyword: 5, semantic: 2, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: server/services/taskService.ts:3-24 TaskService; client/src/components/TaskList.tsx:7-30 TaskList; server/routes/tasks.ts:1-32
- **ts-api-signup** (api_endpoint) “route that registers a new account”
  - expected: server/routes/users.ts
  - ranks: keyword: —, semantic: 1, hybrid: 5, hybrid+rerank: 6
  - hybrid+rerank top 3: server/routes/tasks.ts:1-32; server/services/userModel.ts:9-14; client/src/api/client.ts:18-37 apiFetch
- **ts-x-auth** (cross_file) “how are API requests authenticated between the client and the server”
  - expected: server/middleware/auth.ts requireAuth; client/src/api/client.ts apiFetch
  - ranks: keyword: 6, semantic: 1, hybrid: 1, hybrid+rerank: 1
  - hybrid+rerank top 3: client/src/api/client.ts:18-37 apiFetch; server/middleware/auth.ts:1-3; server/middleware/auth.ts:9-22 requireAuth
- **ts-sem-validate** (semantic) “Which code validates API request data?”
  - expected: server/middleware/validate.ts validateBody
  - ranks: keyword: —, semantic: 1, hybrid: 2, hybrid+rerank: 3
  - hybrid+rerank top 3: client/src/api/client.ts:18-37 apiFetch; server/middleware/auth.ts:5-7 AuthedRequest; server/middleware/validate.ts:6-23 validateBody
- **ts-sem-logout** (semantic) “log the user out when the server responds unauthorized”
  - expected: client/src/api/client.ts apiFetch; client/src/api/client.ts logout
  - ranks: keyword: —, semantic: 1, hybrid: —, hybrid+rerank: —
  - hybrid+rerank top 3: server/routes/users.ts:14-34; server/middleware/auth.ts:9-22 requireAuth; server/services/userModel.ts:3-7 User
- **ts-sem-done** (semantic) “mark a task as done”
  - expected: server/services/taskService.ts TaskService.completeTask; server/routes/tasks.ts
  - ranks: keyword: 5, semantic: 2, hybrid: 2, hybrid+rerank: 2
  - hybrid+rerank top 3: client/src/components/TaskList.tsx:5-5 Task; server/services/taskService.ts:3-24 TaskService; server/services/taskModel.ts:12-20
