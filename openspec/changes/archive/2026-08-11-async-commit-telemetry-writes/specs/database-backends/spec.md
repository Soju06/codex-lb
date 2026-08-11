## ADDED Requirements

### Requirement: Telemetry write transactions relax commit durability on PostgreSQL

A write transaction is classified as a **telemetry write** when it only appends observability rows whose loss on a database-server crash changes nothing about accounting semantics: request-log inserts (`request_logs`) and usage-history appends (`usage_history`, `additional_usage_history`). API-key usage-reservation accounting is explicitly NOT telemetry (see the reservation-durability requirement below).

On PostgreSQL, every telemetry write transaction MUST execute `SET LOCAL synchronous_commit = off` within the transaction itself, so its commit does not wait for the synchronous WAL flush. The relaxation MUST be transaction-scoped (`SET LOCAL`, never `SET`): it reverts automatically at COMMIT or ROLLBACK and MUST NOT leak onto the pooled connection. Because PostgreSQL only emits a WARNING — and applies nothing — when `SET LOCAL` runs outside a transaction, the relaxation MUST be issued through the transaction's own session (SQLAlchemy autobegin opens the transaction at that statement when none is open yet). On SQLite and any other non-PostgreSQL dialect the relaxation MUST be a no-op.

The accepted loss contract is: after a PostgreSQL server crash, telemetry rows committed within the final unflushed WAL window (bounded by three times `wal_writer_delay` — up to ~600 ms at the default 200 ms setting) may be lost. Configuration writes — account, API-key, limit, and settings mutations, schema migrations, scheduler coordination state — MUST NOT relax commit durability.

#### Scenario: Relaxation applies inside the telemetry write transaction

- **GIVEN** a PostgreSQL backend and a telemetry write transaction that has issued the relaxation
- **WHEN** `SHOW synchronous_commit` is executed within the same transaction
- **THEN** it reports `off`

#### Scenario: Session durability is restored after commit or rollback

- **GIVEN** a PostgreSQL session whose current transaction relaxed commit durability
- **WHEN** that transaction commits or rolls back and a subsequent statement runs `SHOW synchronous_commit`
- **THEN** it reports the session default (`on`)

#### Scenario: Relaxation outside a transaction has no effect

- **GIVEN** a PostgreSQL connection in autocommit mode (no open transaction)
- **WHEN** `SET LOCAL synchronous_commit = off` is executed followed by `SHOW synchronous_commit`
- **THEN** the setting does not stick (`on` is reported), which is why the relaxation is issued through the transaction-owning session

#### Scenario: Telemetry write paths emit the relaxation on PostgreSQL

- **GIVEN** a PostgreSQL backend
- **WHEN** a request log is inserted or a usage-history entry is appended
- **THEN** the statements executed by that transaction include `SET LOCAL synchronous_commit = off` before the commit

#### Scenario: Configuration writes keep full durability

- **GIVEN** a PostgreSQL backend
- **WHEN** a configuration write runs (for example creating or updating an API key or account)
- **THEN** its transaction never executes `SET LOCAL synchronous_commit = off`

#### Scenario: SQLite backends are unaffected

- **GIVEN** a SQLite backend (file or `:memory:`)
- **WHEN** any telemetry write path invokes the durability relaxation helper
- **THEN** no statement is emitted and SQLite durability remains governed by its existing PRAGMA configuration

### Requirement: API-key usage-reservation accounting retains full commit durability

API-key usage-reservation writes — reservation creation, settlement (finalize/fail/release, including the limit-counter adjustments riding the same transaction), and the scheduler's stale-reservation release — MUST NOT relax commit durability. Their transactions MUST NOT execute `SET LOCAL synchronous_commit = off`.

Rationale: the "crash loses the in-flight request anyway" argument that justifies relaxing telemetry writes does not hold for reservation accounting on external or highly-available PostgreSQL. A database failover there does not kill in-flight application requests: the application receives the commit acknowledgement, the request completes, and it is served to the caller. If that acked settlement commit is lost in the failover, the reservation stays `reserved`, the stale-reservation release later reverses the limit counters and records zero actual usage, and a request that actually completed disappears from token, cost, and rate-limit accounting — violating the settlement invariant. Stale-release batches mutate the same ledger and MUST keep the same durability so that a release's durability never depends on which path settles the row.

#### Scenario: Reservation creation keeps full durability

- **GIVEN** a PostgreSQL backend
- **WHEN** a usage reservation is created
- **THEN** the statements executed by that transaction never include `SET LOCAL synchronous_commit = off`

#### Scenario: Reservation settlement keeps full durability

- **GIVEN** a PostgreSQL backend holding a `reserved` usage reservation
- **WHEN** the reservation is settled (finalized, failed, or released)
- **THEN** the statements executed by that transaction never include `SET LOCAL synchronous_commit = off`

#### Scenario: Stale-reservation release keeps full durability

- **GIVEN** a PostgreSQL backend holding a stale usage reservation (heartbeat stopped or past the maximum age)
- **WHEN** the stale-reservation release settles a batch (status flip to `released` plus its limit-counter adjustments)
- **THEN** no batch transaction executes `SET LOCAL synchronous_commit = off`
