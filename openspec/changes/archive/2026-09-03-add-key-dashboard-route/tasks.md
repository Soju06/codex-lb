## 1. Backend Contract

- [x] 1.1 Add a typed key-dashboard service and privacy-safe request-log schemas, and verify unit/schema checks cover the allowlisted response fields
- [x] 1.2 Add `GET /api/key-dashboard/request-logs` with unconditional Bearer API-key authentication, server-owned key scoping, newest-first pagination, and dashboard error formatting
- [x] 1.3 Register the key-dashboard context/router and verify integration tests cover missing, invalid, inactive, and valid API keys
- [x] 1.4 Add ownership/redaction regression coverage proving another key's logs and all account/API-key/client/routing identity fields are absent

## 2. Standalone Frontend

- [x] 2.1 Refactor the top-level route boundary so `/key-dashboard` bypasses `AuthGate` and does not mount administrator layout data consumers
- [x] 2.2 Add strict Zod contracts and API functions that fetch `/v1/usage` plus privacy-safe logs in parallel without cookies or the global 401 handler
- [x] 2.3 Build the masked API-key entry screen and in-memory credential lifecycle without URL or durable-storage persistence
- [x] 2.4 Render lifetime stats and the fixed privacy-safe recent-request grid with refresh, pagination, disconnect, loading, empty, and error states
- [x] 2.5 Add localized copy for all supported locale bundles and verify no new core navigation item is introduced

## 3. Regression Coverage and Validation

- [x] 3.1 Add frontend integration tests proving the route skips password-session auth, sends Bearer credentials, renders scoped stats/logs, and omits Account/API Key columns
- [x] 3.2 Run focused backend/frontend tests, frontend typecheck/lint/build, `git diff --check`, and strict OpenSpec validation
