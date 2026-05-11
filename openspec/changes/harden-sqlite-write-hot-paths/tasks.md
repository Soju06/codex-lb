## 1. Implementation

- [x] 1.1 Audit live health/logs and SQLite runtime settings without restarting the service
- [x] 1.2 Identify uncovered write-hot paths from source and live log symptoms
- [x] 1.3 Centralize bounded SQLite write retry with rollback and local serialization
- [x] 1.4 Harden request-log writes and request-log model rewrites
- [x] 1.5 Harden sticky-session write and cleanup paths
- [x] 1.6 Serialize existing retry attempts for API-key reservation, bridge ownership, and ring liveness writes
- [x] 1.7 Add a small live recurrence snapshot probe

## 2. Verification

- [x] 2.1 Add focused unit coverage for request-log SQLite lock retry
- [x] 2.2 Add focused unit coverage for sticky-session SQLite lock retry
- [x] 2.3 Run focused unit tests for SQLite retry, request logs, sticky sessions, API-key reservation, durable bridge, and ring heartbeat
- [x] 2.4 Run focused `ruff` and `ty` checks for touched files
- [x] 2.5 Run `openspec validate harden-sqlite-write-hot-paths --type change --strict`
- [x] 2.6 Run `openspec validate --specs`
- [x] 2.7 Run the live snapshot probe against `codex-lb-direct`
