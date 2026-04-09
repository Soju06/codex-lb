## Why

Single-instance SQLite deployments do not need persisted bridge ring membership, but the runtime still attempts periodic ring heartbeat writes. Under local Codex traffic this can contend with request-log and reservation writes, producing repeated `database is locked` errors and suppressing request-log persistence.

## What Changes

- Skip persisted bridge ring membership at runtime for SQLite-backed single-instance deployments.
- Keep HTTP bridge ownership logic on the static single-node ring when persisted membership is disabled.
- Make API-key reservation release best-effort so cleanup failures do not break otherwise successful responses.

## Impact

- Reduces SQLite write contention and noisy heartbeat stack traces in local/container deployments.
- Preserves multi-instance dynamic ring behavior for non-SQLite deployments.
- Prevents post-response cleanup failures from surfacing as user-visible errors.
