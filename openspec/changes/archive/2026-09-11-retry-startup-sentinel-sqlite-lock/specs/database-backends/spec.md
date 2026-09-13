## ADDED Requirements

### Requirement: Startup sentinel writes tolerate and name transient SQLite lock contention

The startup writes that insert-if-absent into `runtime_sentinels` — the encryption-key fingerprint stamp and the hard-sticky outage-grace seed — MUST retry a transient SQLite lock failure (`database is locked` / `database is busy`) on one shared bounded budget of three retries totalling under one second, and MUST reset a session whose transaction the failed attempt left dirty before the next attempt. Each of these writes is the first statement of its transaction on a startup-fresh connection, so `BEGIN IMMEDIATE` cannot pre-acquire the writer slot for it. A lock failure that outlives the budget MUST preserve the call site's existing outcome rather than newly degrading or newly failing startup, and an `OperationalError` that is not a lock failure MUST propagate without being retried, so the seed's existing not-yet-migrated (`no such table`) degrade to a no-op still applies. Exhausting the budget MUST be reported at WARNING, and each retry at DEBUG, with the driver's extended result-code name (`sqlite_errorname`, or its absence) and the elapsed time — the two mechanisms behind `database is locked` are otherwise indistinguishable, since `SQLITE_BUSY_SNAPSHOT` returns immediately while `SQLITE_BUSY` returns only after the full busy timeout. These reports MUST NOT include the values being written.

#### Scenario: A transient lock on the outage-grace seed is retried
- **GIVEN** the startup hard-sticky outage-grace seed whose sentinel stamp fails once with `database is locked`
- **WHEN** startup runs the seed
- **THEN** the failed transaction is rolled back and the stamp is retried within the shared budget
- **AND** the backfill completes on the retry

#### Scenario: A lock that outlives the budget keeps the existing outcome
- **GIVEN** a startup sentinel write whose every attempt within the budget fails with `database is locked`
- **WHEN** the budget is exhausted
- **THEN** the failure propagates and startup fails, as it did before the retry existed

#### Scenario: A non-lock operational error is not retried
- **GIVEN** a database whose `runtime_sentinels` table does not exist yet
- **WHEN** the outage-grace seed runs
- **THEN** the error is not retried
- **AND** the seed rolls back and returns without stamping, exactly as before

#### Scenario: The give-up report classifies the lock failure
- **GIVEN** a startup sentinel write that exhausts its retry budget
- **WHEN** the exhaustion is reported
- **THEN** the report names the write, the driver's `sqlite_errorname` (or its absence), and the elapsed time
- **AND** the report contains none of the values the write was stamping
