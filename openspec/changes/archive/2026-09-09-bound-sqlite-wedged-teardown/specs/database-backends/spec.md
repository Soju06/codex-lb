## ADDED Requirements

### Requirement: Wedged SQLite session teardown is bounded and reclaimed

File-backed SQLite session teardown (rollback and close) MUST use an initial bounded wait derived from the busy timeout, shielded from caller cancellation, followed by a bounded completion grace for observing successful teardown. Successful completion observed within grace MUST avoid reclamation. A task still pending, cancelled or failed after that observation MUST retain session fencing, interruption/invalidation attempts for captured open connections, and owned late-cleanup bookkeeping. Already-closed handles MUST not be reclaimed again. Cleanup diagnostics MUST preserve available watchdog identifiers, including deferred held duration, owning task and first/last writes. Failed cleanup MUST be reported without claiming guaranteed writer-slot release or a proven permanent hold. Late completion MUST not produce unretrieved errors; deferred bookkeeping close MUST remain tracked and drained at database shutdown. PostgreSQL teardown semantics MUST remain unchanged. In-memory SQLite MUST retain unbounded teardown without reclamation.

#### Scenario: A wedged rollback no longer starves every other writer

- **GIVEN** a session holding an open SQLite write transaction whose rollback wedges during teardown
- **WHEN** the initial deadline and completion grace pass without successful completion
- **THEN** the service interrupts and invalidates the captured open connection through the existing reclaim owner
- **AND** when that cleanup releases the native connection, another writer can acquire the writer slot

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
