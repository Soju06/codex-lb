## 1. Regression

- [x] 1.1 Cover matched-limit PATCH preservation through the API route with committed usage and reset state.
- [x] 1.2 Add deterministic service coverage for retrying a transient SQLite lock after rollback.

## 2. Implementation

- [x] 2.1 Preserve `current_value` and `reset_at` for matched limits unless `resetUsage` is requested.
- [x] 2.2 Retry the complete API-key update read/build/write transaction after transient SQLite lock or snapshot-conflict errors.

## 3. Verification

- [x] 3.1 Run focused API-key unit and integration tests.
- [x] 3.2 Run changed-file Ruff, format, type, and strict OpenSpec checks.
