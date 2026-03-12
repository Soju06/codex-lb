## 1. Routing behavior

- [x] 1.1 Use inbound `session_id` as the sticky routing key for Codex backend Responses requests
- [x] 1.2 Use inbound `session_id` as the sticky routing key for Codex compact requests
- [x] 1.3 Derive the compact upstream account header from the current refreshed account on every compact attempt

## 2. Regression coverage

- [x] 2.1 Add an integration test that proves a Codex thread stays pinned across response and compact requests when dashboard sticky threads are disabled
- [x] 2.2 Add an integration test that proves compact retry uses the refreshed provider account header after a 401 refresh

## 3. Spec updates

- [x] 3.1 Document Codex `session_id` routing affinity for backend Responses and compact requests
- [x] 3.2 Document that compact retries use the refreshed upstream account identity
