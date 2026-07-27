## Why

A single successful `/wham/usage` response can produce primary, secondary, and monthly history rows, but each row is currently committed under its own SQLite writer section. That multiplies durable commits and lock acquisitions while also allowing readers to observe only part of one upstream snapshot if a later row fails.

## What Changes

- Persist all standard usage-window rows derived from one account response with one repository call and one database transaction.
- Give every row in that account snapshot the same capture timestamp.
- Roll back the entire standard-window snapshot when any row cannot be persisted, leaving the caller-owned session usable.
- Keep independent single-row ingestion paths and additional per-model usage-history semantics unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: define atomic persistence for the standard usage windows returned for one account refresh.

## Impact

- Affected code: `app/modules/usage/repository.py`, the background usage repository adapter, and `app/modules/usage/updater.py`.
- Affected storage: inserts into `usage_history`; no schema or migration changes.
- APIs, configuration, dashboard rendering, and upstream request cadence remain unchanged.
