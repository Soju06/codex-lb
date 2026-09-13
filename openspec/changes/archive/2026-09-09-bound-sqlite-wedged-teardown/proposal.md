## Why

Issue #1682, part 2 of the plan. Session teardown shields rollback/close unboundedly (`app/db/session.py`), so a wedged teardown pins SQLite's single writer slot with nothing to reclaim it: every writer — including the `scheduler_leader` INSERT that would re-establish leadership — surfaces `database is locked` until the wedge spontaneously resolves (~17 minutes in the report). Part 1 (`report-sqlite-long-write-holders`) made the holder attributable; the teardown itself must now be bounded. Crucially, abandoning the wedged await alone releases nothing — the aiosqlite worker thread still holds the lock — so the bound must come with reclaiming the connection.

## What Changes

- The shielded rollback/close teardown gets a hard deadline on file-backed SQLite (one-sixth of the busy timeout, 5s — reclaimed well before other writers exhaust their 30s busy timeout). PostgreSQL teardown semantics are untouched, and in-memory SQLite keeps the unbounded path: its one shared StaticPool connection is the whole database (invalidation would destroy it) and cannot starve other writers.
- A teardown that misses the deadline is reclaimed, not merely abandoned: the driver connection is interrupted (aborting the C-level call the aiosqlite worker is stuck in) and the connection is invalidated — terminating it at the pool disposes the worker and hard-closes the underlying `sqlite3` connection, which releases the writer slot and guarantees the connection is never handed out again.
- The reclaim report carries part 1's watchdog identifiers (held duration, owning task, first/last write statements), including when the watchdog had already deferred them into its pending report because the wedge is inside the transaction-ending call itself — invalidation would otherwise suppress that deferred report.
- A wedged session is fenced: later teardown attempts return immediately instead of driving the session concurrently with the abandoned work, and once the abandoned teardown finishes late the session is closed for bookkeeping — a deferred task owned until completion and drained at `close_db`, never fire-and-forget.
- No new settings: the deadline derives from the existing busy timeout.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-backends`: a wedged SQLite session teardown is bounded and its connection reclaimed so the writer slot is released.
