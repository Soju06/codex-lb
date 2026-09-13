## ADDED Requirements

### Requirement: SQLite write-lock stalls are attributable

When a SQLite write transaction holds the writer slot longer than the configured busy timeout, the system MUST report it at WARNING once the transaction has actually ended — the end-of-transaction call itself can be the stall, so the report MUST be deferred to the first proof the DBAPI transaction is over (the connection's next transaction, or its return to the pool) and MUST include that call in the measured hold, including the held duration, whether it committed or rolled back, the owning task where available, and the first and last write statements it executed. The window MUST be measured from the completion of the transaction's first successful write statement — including a bare `BEGIN IMMEDIATE`/`BEGIN EXCLUSIVE`, which acquires the writer slot with no DML — a statement still waiting in the busy timeout has not acquired the slot, so a victim of the stall is never reported as its holder — and read-only transactions, which never take the writer slot in WAL, are never reported. The watchdog MUST NOT raise into the query path and MUST NOT require configuration.

#### Scenario: The starving writer is identified when it finally ends

- **GIVEN** a write transaction that held the writer slot past the busy timeout while other writers surfaced `database is locked`
- **WHEN** it commits or rolls back
- **THEN** a warning reports its duration, outcome, task, and first/last write statements

#### Scenario: A BEGIN IMMEDIATE holder with no DML is attributed

- **GIVEN** a transaction that acquired the writer slot via `BEGIN IMMEDIATE` and ran only reads
- **WHEN** it holds past the busy timeout
- **THEN** it is reported like any other write holder

#### Scenario: A failed commit is not reported as a durable commit

- **GIVEN** a write transaction whose DBAPI commit raises and is rolled back
- **WHEN** the report fires
- **THEN** its outcome states the commit failed and rolled back

#### Scenario: A stalled commit or rollback is inside the measured hold

- **GIVEN** a write transaction whose commit or rollback call itself stalls past the busy timeout
- **WHEN** the connection next begins a transaction or returns to the pool
- **THEN** the report fires with the stall included in the held duration

#### Scenario: A victim waiting out the busy timeout is not reported as the holder

- **GIVEN** a write statement that spends the busy timeout waiting for the slot and fails with `database is locked`
- **WHEN** its transaction rolls back
- **THEN** no long-write report attributes the wait to that transaction

#### Scenario: Healthy traffic is silent

- **WHEN** read-only transactions and writes completing under the threshold run
- **THEN** no long-write report is produced
