## ADDED Requirements

### Requirement: Wedged SQLite session teardown is bounded and reclaimed

Session teardown (rollback and close) on file-backed SQLite MUST complete within a hard deadline derived from the busy timeout while remaining shielded from the caller's cancellation. A teardown that misses the deadline MUST NOT be merely abandoned — the aiosqlite worker thread would keep holding the writer slot — it MUST be reclaimed: the driver connection is interrupted to abort the call the worker is stuck in, and the connection is invalidated so the worker is disposed, the underlying `sqlite3` connection is hard-closed releasing the writer slot, and the connection can never be handed out again. The reclaim MUST be reported with the long-write watchdog's identifiers where available (held duration, owning task, first and last write statements), including identifiers the watchdog already deferred into its pending report. A session whose teardown was abandoned MUST be fenced from further teardown attempts, the abandoned work finishing late MUST NOT surface unretrieved errors, and the deferred bookkeeping close MUST be owned until completion (drained at database shutdown, never fire-and-forget). PostgreSQL teardown semantics MUST remain unchanged, and in-memory SQLite — whose single shared connection is the entire database and cannot starve other writers — MUST keep the unbounded teardown and never be reclaimed.

#### Scenario: A wedged rollback no longer starves every other writer

- **GIVEN** a session holding an open SQLite write transaction whose rollback wedges during teardown
- **WHEN** the teardown deadline passes
- **THEN** teardown returns, the connection is interrupted and invalidated, and another writer — such as the leader-election `scheduler_leader` INSERT — acquires the writer slot immediately instead of surfacing `database is locked`

#### Scenario: The reclaim is attributed with the watchdog's identifiers

- **GIVEN** a wedged teardown whose transaction ran write statements tracked by the long-write watchdog
- **WHEN** the connection is reclaimed
- **THEN** the report names the held duration, owning task, and first/last write statements, even though invalidation prevents the watchdog's own deferred report from firing

#### Scenario: A wedged session cannot be driven concurrently

- **GIVEN** a session whose teardown was abandoned as wedged
- **WHEN** teardown is attempted again
- **THEN** it returns immediately, and the session is closed for bookkeeping only after the abandoned teardown finishes late

#### Scenario: PostgreSQL teardown is untouched

- **GIVEN** a session bound to a non-SQLite dialect
- **WHEN** its rollback or close outlives the SQLite deadline
- **THEN** the teardown still awaits completion unboundedly and no connection is reclaimed

#### Scenario: The shared in-memory SQLite connection is never reclaimed

- **GIVEN** a session bound to an in-memory SQLite database, whose one shared connection is the entire database
- **WHEN** its teardown outlives the deadline
- **THEN** the teardown still awaits completion unboundedly and the connection is never invalidated, preserving schema and data for later sessions

#### Scenario: The bound never abandons healthy teardown

- **WHEN** rollback and close complete within the deadline
- **THEN** teardown behaves exactly as before, including re-raising the completed call's exception to the existing swallow points
