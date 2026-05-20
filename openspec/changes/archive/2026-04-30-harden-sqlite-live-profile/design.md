## Context

`codex-lb` uses SQLite by default and already applies SQLite resilience settings in the engine setup: WAL mode for file databases, `synchronous=NORMAL`, foreign keys, and a busy timeout. Those settings help normal local usage, but they do not remove SQLite's single-writer constraint.

The direct Docker runtime can run an addressable bridge worker pool with multiple backend worker processes. That is correct for HTTP response continuity, but when the selected backend is SQLite every process can still race on the same database file. Write-heavy paths include request reservation bookkeeping and bridge/ring liveness updates.

## Goals

- Keep SQLite as the default and supported local backend.
- Define a safe live profile for operators who choose SQLite intentionally.
- Convert transient SQLite write-lock pressure into bounded wait/retry behavior before surfacing a 5xx.
- Keep PostgreSQL as the recommended higher-concurrency profile without requiring it for basic local startup.

## Non-Goals

- Replace SQLite as the default.
- Introduce a new database URL alias or separate database selector.
- Require a schema migration for this hardening change.
- Disable bridge worker pools globally.

## Proposed Approach

The SQLite live profile has two layers:

1. Deployment guidance constrains concurrency when SQLite is selected. The conservative Docker profile should set `CODEX_LB_UVICORN_WORKERS=1`, or otherwise make the operator explicitly opt into a hardened multi-worker SQLite mode.
2. Runtime writes that are expected during request admission or bridge liveness must handle transient SQLite lock errors with bounded retry/backoff. The retry budget should be short enough to preserve request budgets and long enough to cover normal writer overlap.

For future multi-worker SQLite support, the runtime can satisfy the same contract with a single writer queue/process or equivalent serialization for write-hot paths. That should be treated as an explicit design, not an accidental side effect of pool settings.

## Failure Modes

- If SQLite is used with many worker processes and no write serialization, request admission can return 500 during bursts.
- If retry is unbounded, lock pressure can hide a dead writer or consume the upstream request budget.
- If deployment guidance only recommends PostgreSQL, operators who must stay on SQLite lack a supported path.

## Example

A conservative SQLite Docker profile keeps the default database URL and limits request workers:

```bash
CODEX_LB_DATABASE_URL=sqlite+aiosqlite:////var/lib/codex-lb/store.db
CODEX_LB_UVICORN_WORKERS=1
```

Higher concurrency should use the PostgreSQL profile covered by the database backend guidance.
