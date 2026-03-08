## 1. Routing behavior

- [x] 1.1 Use inbound `session_id` as the sticky routing key for Codex backend Responses requests
- [x] 1.2 Use inbound `session_id` as the sticky routing key for Codex compact requests

## 2. Regression coverage

- [x] 2.1 Add an integration test that proves a Codex thread stays pinned across response and compact requests when dashboard sticky threads are disabled

## 3. Spec updates

- [x] 3.1 Document Codex `session_id` routing affinity for backend Responses and compact requests
