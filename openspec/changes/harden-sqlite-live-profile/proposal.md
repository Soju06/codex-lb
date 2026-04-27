## Why

SQLite remains the zero-config backend, but the live Docker profile can be configured with multi-worker bridge serving. Under SQLite, concurrent request workers and background heartbeat writers share a single writer lock, so bursts can surface as `database is locked` failures and transient 500s instead of bounded latency.

Operators who intentionally stay on SQLite need a spec-backed live profile that limits write contention and makes transient lock pressure recoverable.

## What Changes

- define a SQLite live profile that treats single-writer constraints as part of the supported operating model
- require bounded retry/backoff for transient SQLite lock errors on write-hot request paths before returning a server error
- require deployment guidance to distinguish the SQLite-conservative profile from higher-concurrency PostgreSQL guidance
- preserve SQLite as the default backend for simple local startup

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `database-backends`: add SQLite live-profile requirements for write contention controls and bounded lock retry
- `deployment-installation`: add Docker guidance requirements for a SQLite-conservative runtime profile

## Impact

- Specs: `database-backends`, `deployment-installation`
- Future runtime work: SQLite lock retry/backoff around write-hot paths such as usage reservations and bridge/ring heartbeat writes
- Future docs/config work: README, `.env.example`, and direct Docker guidance for SQLite-safe worker settings
- No immediate API, schema, or data migration change
