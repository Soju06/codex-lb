## Why

Single-instance SQLite deployments can emit repeated `database is locked` errors from bridge ring heartbeat writes. The failures add log noise and can interfere with request-side bookkeeping such as request logs and API key reservation cleanup.

## What Changes

- Disable dynamic bridge-ring DB registration/heartbeat for SQLite-backed deployments.
- Prefer the existing static single-instance ring behavior when dynamic membership is unnecessary.
- Keep bridge transport behavior unchanged for explicit static rings and non-SQLite deployments.

## Impact

- Local SQLite deployments stop writing periodic `bridge_ring_members` heartbeat rows.
- Multi-instance or non-SQLite deployments keep existing dynamic ring behavior.
