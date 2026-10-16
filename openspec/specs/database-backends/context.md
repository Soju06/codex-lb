## Overview

codex-lb is designed to be SQLite-first for simple local usage and container defaults. SQLite-specific resilience behavior (integrity checks, WAL tuning, recovery tooling) remains valuable for the default mode.

For higher concurrency or infrastructure-managed deployments, PostgreSQL support is enabled through SQLAlchemy async URLs using `asyncpg`.

## Decisions

- Keep SQLite as default to preserve zero-config startup.
- Accept PostgreSQL through `CODEX_LB_DATABASE_URL` only; no new configuration key aliases.
- Keep SQLite-specific recovery tooling SQLite-only; PostgreSQL operations should use PostgreSQL-native backup/recovery practices.
- Default SQLite startup validation to `quick` so normal boots stay fast while operators can still opt into `full` or `off`.

## Operational Notes

- SQLite default URL: `sqlite+aiosqlite:///~/.codex-lb/store.db`
- SQLite startup check mode: `CODEX_LB_DATABASE_SQLITE_STARTUP_CHECK_MODE=quick|full|off` (default `quick`)
- PostgreSQL example URL: `postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb`
- Pool sizing (`database_pool_size`, `database_max_overflow`) applies to PostgreSQL engine creation; the pool checkout timeout (30 s) and connection recycle window (1800 s) are fixed constants in `app/db/session.py` (issue #1340 phase 3).
- The background/request-adjacent DB engine always derives its pool sizing from `database_pool_size` and `database_max_overflow`; it isolates background-task checkouts rather than being sized independently.
- A supported application replica runs one worker with two independently pooled PostgreSQL engine roles (request path and background tasks), so its maximum application connection count is `2 * (database_pool_size + database_max_overflow)`. The owned CLI pins one Uvicorn worker even if `WEB_CONCURRENCY` is present; custom multi-worker launchers are unsupported.

## Example

Use PostgreSQL while keeping all other defaults:

```bash
CODEX_LB_DATABASE_URL=postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb codex-lb
```

Use SQLite with explicit full startup validation:

```bash
CODEX_LB_DATABASE_SQLITE_STARTUP_CHECK_MODE=full codex-lb
```

## SQLite teardown completion grace

A file-backed SQLite worker can finish rollback or close while the event loop is delayed and has not delivered the completion callbacks. The initial teardown bound therefore begins a separate bounded opportunity to observe successful task completion. Only success avoids reclamation; failed, cancelled or still-pending teardown retains the pre-teardown connection snapshot, session fence and tracked cleanup owner. The existing 0.75-second grace can add that delay before a genuine wedge is reclaimed; it is not a measured optimum.

The caller owns teardown during both the initial wait and grace. Only reclamation registers abandoned work in the database shutdown registry, so a concurrent `close_db()` can finish during either observation window. Grace extends that existing exposure by up to 0.75 seconds. The application's separate request and leader-release drain results still govern whether shutdown may be marked clean.

For example, a native rollback may finish while the loop is blocked across the initial bound. Grace observes its success, normal session close proceeds and a second writer can commit. Real file-SQLite regression checks reproduce this ordering on asyncio and uvloop; disabling grace restores false interruption. This demonstrates a reachable ordering failure, not the cause or duration of historical production stalls.

Warnings distinguish observed completion, attempted reclamation and failed cleanup. Elapsed time at the initial bound is not an event-loop lag measurement, and a failed invalidation does not prove a permanent writer hold. No database migration, pool change or operator setting is required. The [owning requirement](spec.md#requirement-a-completed-sqlite-teardown-is-never-reclaimed) defines the completion and cleanup contract.

The invalidation warning covers exceptions raised by `connection.invalidate()`. SQLAlchemy 2.0.52 catches ordinary driver-close exceptions inside `Pool._close_connection` and logs them at ERROR, so those failures retain the pool's diagnostic owner. Finalization can also run for a task already terminal during grace; its message reports completion without claiming that the task finished after reclamation.

## Cancelled SQLite statements retained by tracebacks

A cancellation during a write with RETURNING can leave its unfinished sqlite3 cursor reachable from the cancellation traceback. Closing the connection alone can leave that statement holding the writer lock until cursor collection. This was reproduced with Python 3.14 and SQLAlchemy 2.0.52 and 2.1.4, including through both real bridge terminal transcript formats. It explains a reachable persistent lock failure; it does not identify the exact cursor held in an already-running incident process.

File-backed main and background engines use a connection factory that weakly tracks SQLAlchemy-created cursors and explicitly closes them before raw connection close. Cleanup stays on aiosqlite's worker thread. Weak tracking avoids retaining otherwise dead cursors. Memory SQLite and PostgreSQL engine configuration are unchanged. Raising busy_timeout or pinning either tested SQLAlchemy version alone cannot release this stranded statement.

An ordinary cursor-close exception does not stop cleanup of the remaining cursors. Teardown attempts every cursor and the base connection close, then propagates the first cleanup exception. A cursor whose native finalization itself fails cannot be assumed released; the remaining cursors still get their cleanup opportunity.

For example, a terminal transcript write can hit its persistence deadline while an INSERT RETURNING is executing. After cancellation unwinds and teardown completes, fenced settlement and scheduler lease writes can proceed even while the cancellation traceback remains referenced. A worker still actively blocked is subject to the existing teardown/interruption bounds; cursor finalization takes effect when the queued close runs.

The fix needs no migration or new setting. Restart with the patched build to replace existing connections, then verify real bridge writes and leader renewals; a healthy HTTP endpoint alone does not demonstrate recovery. See the [cleanup requirement](spec.md#requirement-cancelled-sqlite-writes-release-statement-locks-during-connection-teardown).
