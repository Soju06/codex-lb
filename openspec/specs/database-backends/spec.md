# database-backends Specification

## Purpose

Define supported database backend wiring so local, Helm, SQLite, and external PostgreSQL deployments behave consistently.
## Requirements
### Requirement: Helm external PostgreSQL wiring resolves a non-empty database URL

When the Helm chart deploys with `postgresql.enabled=false`, it MUST provide a non-empty `CODEX_LB_DATABASE_URL` to the workload from one of the supported external database inputs. The chart MUST accept a direct `externalDatabase.url`, and it MUST also support reading `database-url` from an operator-provided external database secret reference without requiring the application encryption-key secret to be the same object.

#### Scenario: Direct external database URL is used

- **WHEN** `postgresql.enabled=false`
- **AND** `externalDatabase.url` is non-empty
- **THEN** the rendered workload uses that value for `CODEX_LB_DATABASE_URL`

#### Scenario: External database URL comes from a dedicated secret reference

- **WHEN** `postgresql.enabled=false`
- **AND** `externalDatabase.existingSecret` is set
- **THEN** the rendered workload reads `database-url` from that secret for `CODEX_LB_DATABASE_URL`

### Requirement: PostgreSQL engines validate and recycle pooled connections

When `database_url` resolves to a PostgreSQL backend, the application MUST configure each async engine — both the request-path `engine` and the optional background-task `_background_engine` — with `pool_pre_ping=True` and a finite `pool_recycle` window. This is required so the application detects connections that the PostgreSQL server has silently closed (idle timeout, restart, network reset) before the first real query is dispatched on them, and so connections are cycled before they reach any reasonable upstream keep-alive boundary. The recycle window is the fixed 1800-second application constant in `app/db/session.py`.

Each PostgreSQL statement MUST additionally be bounded by the fixed asyncpg `command_timeout` application constant in `app/db/session.py`, so a query stalled on a half-dead connection surfaces as an error within the bound instead of awaiting indefinitely — pre-ping only protects the first statement after checkout, not a connection that dies mid-statement. This bound covers the dominant stall (a dispatched statement whose response never arrives); asyncpg's timeout handling then cancels the statement and raises to the caller, releasing whatever application lock the caller held. The bound is best-effort against a fully blackholed peer: asyncpg's post-timeout cancellation handshake itself talks to the server, so lock-discipline requirements (resolving settings/DB inputs before acquiring application locks) remain the primary defense and MUST NOT be relaxed on the strength of this bound. Alembic migrations run on their own synchronous engine and are not subject to this bound.

#### Scenario: Stale connections are rejected before checkout

- **WHEN** a pooled connection has been closed by the server while sitting idle
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy issues a pre-ping (`SELECT 1`), detects the dead connection, and transparently replaces it
- **AND** the application returns `200` (or the real business-level result), not `500 server_error` with `asyncpg.InterfaceError: connection is closed`

#### Scenario: Pool recycle bounds connection age

- **WHEN** a pooled connection has been open longer than the fixed 1800-second recycle window
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy discards and replaces the connection before the next query

#### Scenario: Mid-statement connection death cannot wedge its caller

- **WHEN** a statement's connection dies after dispatch (network partition, half-dead peer) and no response arrives, while the server remains reachable for the cancellation handshake
- **THEN** asyncpg cancels the statement and raises within the fixed `command_timeout` bound
- **AND** any application lock held by the caller is released when the error propagates

#### Scenario: The statement bound does not license DB awaits under application locks

- **GIVEN** a code path that would await a settings or database read while holding an application lock
- **WHEN** the path is reviewed against the lock-discipline requirements
- **THEN** the `command_timeout` bound is not accepted as a substitute for resolving the read before acquiring the lock

#### Scenario: SQLite backends are not affected

- **WHEN** `database_url` resolves to a SQLite backend (file or `:memory:`)
- **THEN** neither `pool_pre_ping`, `pool_recycle`, nor `command_timeout` is configured on the engine
- **AND** existing SQLite-specific tuning (PRAGMAs, `busy_timeout`) is unchanged

### Requirement: Database pool controls cover request-adjacent background sessions

The service SHALL size both the main request pool and the
background/request-adjacent session pool from `database_pool_size` and
`database_max_overflow`. The background pool SHALL always derive from those
two settings; it exists to isolate background-task checkouts from the
request pool, not to be sized independently.

#### Scenario: Background pool inherits main pool capacity

- **WHEN** the application creates the background/request-adjacent DB engine for a pooled backend
- **THEN** the background pool uses `database_pool_size` and `database_max_overflow`
- **AND** no separate background pool sizing setting exists

### Requirement: Detached background tasks own their database session lifetime

Detached background tasks MUST own database session lifetime independently from cancellable callers.

A background task that is intentionally decoupled from its caller's lifetime
(for example a singleflight refresh kept alive with `asyncio.shield` so
concurrent waiters share one in-flight operation) MUST NOT perform database work
through a session whose lifetime is owned by the cancellable caller. Such a task
MUST acquire its own session (via `get_background_session()` or an equivalent
caller-independent factory), use it, and release it entirely within the task.

Background refresh schedulers MUST also avoid holding an `AsyncSession` while
performing upstream network I/O. Usage refresh, model-registry refresh, and
reset-credits refresh MUST perform account/usage/settings reads in short
sessions, close those sessions, perform upstream fetches, and reacquire short
sessions only for required database writes.

#### Scenario: Client disconnect during token refresh does not strand a connection

- **GIVEN** a proxy request triggers an account token refresh through `AuthManager.ensure_fresh`
- **AND** the refresh runs as a detached singleflight task held alive by `asyncio.shield`
- **AND** the request that initiated it is bound to a request-scoped background session
- **WHEN** the client disconnects mid-refresh and the request task is cancelled
- **THEN** the refresh task MUST complete its token/status writes against its own session, acquired independently of the cancelled request
- **AND** the request-scoped session MUST close without being used by the refresh task after close
- **AND** no background-pool connection is left checked out after the refresh task finishes

#### Scenario: Non-cancellable callers without network I/O retain the bound-session path

- **GIVEN** a caller whose session is not tied to a client-cancellable request
- **AND** the caller does not hold that session across external network I/O
- **AND** that caller invokes `AuthManager.ensure_fresh` without supplying a refresh session factory
- **WHEN** a token refresh runs
- **THEN** the refresh MAY use the caller's bound session
- **AND** behavior is unchanged from before this requirement

#### Scenario: Accumulated leak no longer exhausts the background pool

- **GIVEN** repeated client disconnects during token refreshes over an extended period
- **WHEN** each disconnect-during-refresh occurs
- **THEN** each refresh task releases its connection back to the background pool
- **AND** the background engine pool (sized from `database_pool_size` + `database_max_overflow`) is not driven to exhaustion by stranded refresh connections
- **AND** `/backend-api/codex/*` requests do not begin returning `500` from `QueuePool limit ... connection timed out` as a result of this path

#### Scenario: Usage refresh fetch runs after the read session closes

- **GIVEN** usage refresh selects an account from the database
- **WHEN** it calls the upstream usage endpoint
- **THEN** the session used to read latest usage, accounts, and settings has already closed
- **AND** usage rows, account status changes, and warm-up attempt/log writes use separate short sessions

#### Scenario: Model registry refresh fetch runs after the account read session closes

- **GIVEN** model registry refresh reads active accounts from the database
- **WHEN** it calls the upstream model discovery endpoint
- **THEN** the account-list session has already closed
- **AND** token refresh and route resolution use independent short sessions when database access is required

#### Scenario: Reset-credits refresh fetch runs after the account read session closes

- **GIVEN** reset-credits refresh reads accounts from the database
- **WHEN** it calls the upstream reset-credits endpoint
- **THEN** the account-list session has already closed
- **AND** route resolution uses an independent short session when database access is required

### Requirement: SQLite usage history supports raw-window latest lookups
SQLite deployments MUST maintain an index that supports latest `usage_history` lookup by raw usage window, account id, and newest recorded sample ordering.

#### Scenario: Secondary usage lookup uses the raw-window latest index
- **GIVEN** the database backend is SQLite
- **AND** `usage_history` contains rows for the `secondary` window
- **WHEN** the dashboard overview asks for latest usage by account for the `secondary` window
- **THEN** SQLite MUST be able to satisfy the raw `window='secondary'` filter with `idx_usage_window_raw_account_latest`
- **AND** the query result MUST remain semantically identical to the previous latest-usage lookup

#### Scenario: Migration is safe after a live hotfix
- **GIVEN** `idx_usage_window_raw_account_latest` was already created manually as a live SQLite hotfix
- **WHEN** the schema migration is applied
- **THEN** the migration MUST complete without failing on duplicate index creation

### Requirement: Persisted reset-window routing setting
Dashboard settings storage SHALL persist `prefer_earlier_reset_window` as a
non-null setting with allowed values `primary` and `secondary`. New and migrated
installations SHALL default the value to `secondary`.

#### Scenario: Existing dashboard settings are migrated
- **GIVEN** an existing dashboard settings row without `prefer_earlier_reset_window`
- **WHEN** migrations are applied
- **THEN** the row has `prefer_earlier_reset_window = "secondary"`

#### Scenario: Settings API rejects unsupported windows
- **WHEN** a settings update requests a reset-window value other than `primary` or `secondary`
- **THEN** the API rejects the payload instead of persisting it

### Requirement: File-backed SQLite engines do not retain idle pooled descriptors

File-backed SQLite main and background async engines MUST use non-pooled connection semantics.

SQLite `:memory:` databases MUST preserve the existing shared-engine behavior
for background sessions so schema state remains visible to background tasks.

Pool sizing (`database_pool_size`, `database_max_overflow`) and the fixed
pool checkout timeout SHALL constrain pooled backends only. They SHALL NOT
be passed to file-backed SQLite engines.

#### Scenario: File SQLite uses NullPool

- **GIVEN** `database_url` resolves to a file-backed SQLite database
- **WHEN** the application creates its main or background async engine
- **THEN** the engine is configured with `NullPool`
- **AND** `pool_size`, `max_overflow`, and `pool_timeout` are not passed
- **AND** existing SQLite PRAGMAs and busy timeout behavior remain enabled

#### Scenario: PostgreSQL pooling is unchanged

- **GIVEN** `database_url` resolves to PostgreSQL
- **WHEN** the application creates its main or background async engine
- **THEN** PostgreSQL pool sizing, overflow, pre-ping, and recycle controls remain configured as before

### Requirement: SQLite account writes share the local writer section

SQLite account mutation paths SHALL enter the shared SQLite writer section
before performing database writes. This includes account import/upsert,
reauthentication upsert, token refresh persistence, status transitions,
account-level dashboard preference writes, and account deletion.

PostgreSQL account mutation paths SHALL preserve their existing transaction and
advisory-lock behavior.

#### Scenario: Account token persistence is serialized on SQLite

- **GIVEN** the deployment uses a file-backed SQLite database
- **WHEN** an account token refresh persists new encrypted token values
- **THEN** the write executes inside the shared SQLite writer section

#### Scenario: Account status persistence is serialized on SQLite

- **GIVEN** the deployment uses a file-backed SQLite database
- **WHEN** an account status transition is persisted
- **THEN** the write executes inside the shared SQLite writer section

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

### Requirement: Asyncpg PostgreSQL sessions pin time zone to UTC

When `database_url` resolves to a PostgreSQL backend through the asyncpg driver, the application MUST configure each SQLAlchemy async engine connection with a database session time zone of `UTC`.

This requirement applies to the request-path `engine`, the optional background
`_background_engine`, and any app-created PostgreSQL async engine that uses the
shared PostgreSQL engine kwargs helper.

#### Scenario: Asyncpg sessions ignore non-UTC database defaults

- **GIVEN** `database_url` uses `postgresql+asyncpg://`
- **AND** the PostgreSQL role, database, container, or server default time zone
  is not UTC
- **WHEN** the application opens a new asyncpg connection through its engine
  configuration
- **THEN** `SHOW TIME ZONE` on that connection reports `UTC`
- **AND** naive UTC datetimes written by the application are interpreted as UTC
  before PostgreSQL stores them in `timestamptz` columns

#### Scenario: SQLite backends are not affected

- **GIVEN** `database_url` resolves to a SQLite backend
- **WHEN** the application creates its async engine
- **THEN** PostgreSQL asyncpg `server_settings` are not configured
- **AND** existing SQLite PRAGMAs, busy timeout, and pooling behavior remain
  unchanged

### Requirement: PostgreSQL connection budgets include every pooled engine

The application SHALL define its per-worker PostgreSQL connection capacity as the configured per-engine pool capacity multiplied by the declared set of independently pooled engine roles. The request-path and background-task engine creation paths MUST each use the shared role-aware PostgreSQL engine factory, and the engine-count budget MUST be derived from those declared roles. The owned server launcher MUST run the supported one worker per replica explicitly, rather than allowing `WEB_CONCURRENCY` to multiply worker processes and their pools.

#### Scenario: One replica reaches configured pool capacity

- **WHEN** both declared PostgreSQL engine roles in one application worker reach `database_pool_size + database_max_overflow`
- **THEN** the worker's aggregate application connection capacity is `2 * (database_pool_size + database_max_overflow)`
- **AND** both engines were created through the role-aware factory counted by that formula

#### Scenario: WEB_CONCURRENCY cannot multiply owned-launcher pools

- **GIVEN** `WEB_CONCURRENCY` is greater than 1
- **WHEN** the application starts through the owned `app.cli` launcher used by Helm
- **THEN** the launcher explicitly starts one Uvicorn worker
- **AND** the replica creates only the request-path and background-task pools
- **AND** operators MUST scale supported deployments through replicas rather than custom multi-worker launchers

#### Scenario: Test database disables pooling

- **WHEN** `CODEX_LB_TEST_DATABASE_URL` selects `NullPool`
- **THEN** pool sizing controls and the production pooled-engine budget do not apply to that test engine

### Requirement: SQLAlchemy-rendered Windows SQLite paths are percent-decoded before opening

When a SQLite database URL is converted to a filesystem path for direct filesystem use (e.g. startup directory creation, startup integrity checks, migration locks, or the usage repository's read-only helper), a path that matches a recognizable SQLAlchemy-rendered Windows form — an encoded drive marker (`<letter>%3A` followed by an encoded or raw path separator) or an encoded UNC prefix (`%5C%5C`) — MUST be percent-decoded before being handed to the filesystem. SQLAlchemy's `URL.render_as_string()` percent-encodes a Windows-style default path (`C:\Users\...` -> `C%3A%5CUsers%5C...`); without decoding, the literal escaped string either fails to open with "unable to open database file" or creates a stray 0-byte database next to the current working directory, which breaks account/usage reads with `no such table`.

Paths that do NOT match those rendered Windows forms MUST be preserved literally. Settings builds the default SQLite URL directly from the configured data directory without URL-encoding it, so a percent sequence in a POSIX or raw Windows path (e.g. `/var/lib/codex%20lb/store.db`) names a real directory and MUST NOT be rewritten by decoding.

#### Scenario: Windows default path resolves to the real file

- **GIVEN** the default SQLite URL on Windows (`sqlite+aiosqlite:///C:\Users\...\store.db`)
- **WHEN** `URL.render_as_string()` percent-encodes it into `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`
- **AND** the path is extracted and decoded
- **THEN** `sqlite3.connect()` receives `C:\Users\...\store.db` (the real file), not the percent-escaped literal

#### Scenario: Encoded drive with URL slash separators resolves to the real file

- **GIVEN** a Windows SQLite URL with an encoded drive colon and normal URL path separators (`sqlite:///C%3A/Users/me/.codex-lb/store.db`)
- **WHEN** the path is extracted and decoded
- **THEN** the filesystem path is `C:/Users/me/.codex-lb/store.db`, not the literal `C%3A/Users/me/.codex-lb/store.db`

#### Scenario: Startup uses the decoded SQLite path

- **GIVEN** a percent-encoded SQLite file URL whose decoded parent directory differs from the percent-literal parent
- **WHEN** `init_db()` prepares the SQLite directory and runs the startup integrity check
- **THEN** the decoded parent directory is created
- **AND** the integrity check receives the decoded database path

#### Scenario: URL normalization preserves decoded Windows path characters

- **GIVEN** an encoded Windows SQLite URL whose decoded database path contains spaces, literal `%`, or `#`
- **WHEN** the URL is normalized for SQLAlchemy consumers
- **THEN** the returned URL contains the real decoded Windows filesystem path
- **AND** filesystem extraction from that normalized URL returns the same decoded path
- **AND** a raw Windows URL containing a literal percent sequence such as `%23` is not decoded unless it first matched a SQLAlchemy-rendered encoded Windows form

#### Scenario: Literal percent sequences in POSIX paths are preserved

- **GIVEN** a POSIX SQLite URL whose path contains a literal percent sequence (`sqlite+aiosqlite:////var/lib/codex%20lb/store.db`) built directly from the configured data directory
- **WHEN** the path is extracted for filesystem use or the URL is normalized
- **THEN** the filesystem path remains `/var/lib/codex%20lb/store.db` and the URL is unchanged (the sequence is not decoded to a space)

#### Scenario: Normalized UNC paths keep fragment characters

- **GIVEN** an encoded UNC SQLite URL whose decoded share path contains a legal `#` character (`sqlite:///%5C%5Cserver%5Cshare%23x%5Cstore.db`)
- **WHEN** the URL is normalized and the filesystem path is then extracted from the normalized URL
- **THEN** the extracted path is `\\server\share#x\store.db`
- **AND** the path is not truncated at the `#` as if it were a URL fragment separator

#### Scenario: POSIX paths are unchanged

- **GIVEN** a POSIX-style SQLite URL (`sqlite+aiosqlite:///var/lib/codex-lb/store.db`)
- **WHEN** the path is extracted and decoded
- **THEN** the result is identical to the input path (no `%` to decode; behavior is a no-op)

#### Scenario: In-memory databases are not treated as file paths

- **GIVEN** a `:memory:` SQLite URL
- **WHEN** the path is extracted
- **THEN** no filesystem path is returned and no file is created

### Requirement: A completed SQLite teardown is never reclaimed

When the initial bounded wait for file-backed SQLite rollback or close expires, the service MUST observe the existing teardown task for a bounded completion grace before reclamation. Only successful task completion observed within that opportunity MUST exempt the session from fencing, driver interruption, connection invalidation and deferred cleanup registration. Normal remaining teardown MUST still run. Failed, cancelled or still-pending tasks MUST retain the existing fencing and tracked reclamation/late-cleanup ownership, using connections captured before teardown; already-closed handles MUST not be interrupted or invalidated again.

A successful-completion warning MUST name the phase, configured initial bound and elapsed time measured at that bound before grace. Diagnostics MUST describe observed task/cleanup outcomes; elapsed time alone MUST NOT be reported as measured event-loop lag, and failed invalidation alone MUST NOT be asserted to prove a permanent writer hold. An observed teardown failure and an exception raised by `connection.invalidate()` MUST be reported at warning level. A teardown-finalization diagnostic MUST report completion without asserting that it occurred after reclamation.

#### Scenario: Real rollback worker completes while the event loop is delayed
- **GIVEN** a file-backed SQLite write transaction whose native rollback finishes while the event loop is blocked across the initial bound
- **WHEN** grace observes successful task completion after the loop resumes
- **THEN** the session is not fenced and no connection is interrupted or invalidated
- **AND** no deferred cleanup is registered, normal close runs, and another writer can commit
- **AND** the warning reports the phase, initial bound and pre-grace elapsed time without claiming measured lag

#### Scenario: Real close worker completes while the event loop is delayed
- **GIVEN** a file-backed SQLite session whose native close finishes while completion callbacks cannot run across the initial bound
- **WHEN** grace observes successful close completion
- **THEN** no reclaim or deferred cleanup is performed and another writer can commit

#### Scenario: Failure or cancellation is not successful completion
- **WHEN** a teardown that outlived the initial bound fails, is cancelled, or remains pending after grace
- **THEN** existing fencing, captured-connection reclamation attempts and owned late cleanup remain in effect
- **AND** already-closed handles are skipped without suppressing an observed teardown failure

#### Scenario: Reclamation diagnostics do not invent a permanent lock
- **WHEN** `connection.invalidate()` raises an exception for a captured open connection
- **THEN** the failure is reported at warning level
- **AND** diagnostics do not assert that a permanent writer hold has been proven

#### Scenario: A task already terminal during grace is finalized
- **GIVEN** teardown failed after releasing its connection and before reclamation began
- **WHEN** the completion callback finalizes the abandoned task
- **THEN** its diagnostic reports completion without describing it as a late finish after reclamation
- **AND** the existing deferred close and cleanup-task ownership remain in effect

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

### Requirement: Default pool sizing preserves raw-slot reserve on default max_connections

The default values of `database_pool_size` and `database_max_overflow` MUST
keep one replica's aggregate application connection capacity —
`(database_pool_size + database_max_overflow) * 2 pooled engines * 1
supported worker` — at or below 80, so a single replica on PostgreSQL's
default `max_connections=100` retains at least 20 raw server slots for
PostgreSQL-reserved connections, the migration path's two-connection peak,
administration, and transient non-application clients.

#### Scenario: Default single replica fits default max_connections

- **WHEN** one replica runs with the default `database_pool_size` and
  `database_max_overflow`
- **THEN** both pooled engines together cap at no more than 80 PostgreSQL
  connections
- **AND** at least 20 raw server slots remain on a default
  `max_connections=100` server

#### Scenario: Operators can still tune the pool

- **WHEN** `CODEX_LB_DATABASE_POOL_SIZE` or `CODEX_LB_DATABASE_MAX_OVERFLOW`
  is set in the environment
- **THEN** the configured values override the defaults for both pooled
  engines

### Requirement: Proxy usage refresh does not retain sessions across upstream I/O

The public proxy usage payload path MUST detach rows loaded for its initial
refresh decision before closing the request-adjacent repository scope. An owned
usage refresh MUST use caller-independent short-lived repositories for freshness
reads, upstream fetches, and required writes; it MUST NOT retain an
`AsyncSession` while waiting for upstream usage I/O. The payload path MUST
reopen a repository scope only after the refresh completes.

#### Scenario: cancelled usage request closes its initial scope

- **GIVEN** `/api/codex/usage` starts an owned usage refresh
- **WHEN** the client request is cancelled while the refresh is in flight
- **THEN** the initial repository scope is closed before the owned refresh runs
- **AND** the owned refresh remains caller-independent and may finish safely
- **AND** the payload-read repository scope is not reopened by the cancelled request

#### Scenario: usage refresh releases its session during upstream fetch

- **GIVEN** an owned usage refresh needs to fetch usage from an upstream service
- **WHEN** the upstream request is in flight
- **THEN** no database session remains checked out for that refresh's read/write work
- **AND** the refresh reacquires short-lived sessions only for required database operations

### Requirement: Account ChatGPT identity lookups are index-supported

Deployments MUST maintain an index (`idx_accounts_chatgpt_account_id`) on
`accounts (chatgpt_account_id)` so that per-snapshot ChatGPT account identity
lookups performed during live usage snapshot settlement do not scan the
accounts heap. On PostgreSQL the migration MUST build the index concurrently
and MUST complete without failing when a valid index of the same name already
exists.

#### Scenario: Identity lookup is index-supported after migration

- **WHEN** database migrations are applied
- **THEN** the `accounts` table includes an index on `chatgpt_account_id` named `idx_accounts_chatgpt_account_id`
- **AND** live usage settlement's unique ChatGPT identity lookup is satisfiable by that index for its filter phase

#### Scenario: Interrupted concurrent build is repaired, not accepted

- **GIVEN** the database backend is PostgreSQL
- **AND** a previous `CREATE INDEX CONCURRENTLY` for `idx_accounts_chatgpt_account_id` was interrupted, leaving an invalid index (`pg_index.indisvalid = false`) under the same name
- **WHEN** the schema migration is applied
- **THEN** the migration MUST drop the invalid index and rebuild it rather than accepting it via `IF NOT EXISTS`

### Requirement: The SQLite startup integrity check is skipped after a recorded clean shutdown

The startup integrity check reads every page of the SQLite file and the
listener MUST NOT bind until it returns, so its cost grows with the store.
Because SQLite is already consistent after a clean close, the system MUST
record how each process left the store and MUST run the startup check only
when the previous process did not record a clean shutdown.

The run state MUST be persisted in a sidecar next to the database file. The
system MUST record `running` during startup and MUST record `clean` only
after the database engines are disposed during an orderly shutdown. The
`clean` record MUST NOT be reachable from a crash, a signal-killed process,
or a failed startup.

Startup MUST read the prior run-state record before mutating it, then MUST
persist `running` before deciding whether a prior `clean` record permits
skipping the integrity check. If the `running` transition cannot be recorded,
startup MUST run the configured check when enabled and MUST NOT trust the
prior `clean` record. When the failed transition has durably removed the
untrusted sidecar, startup MAY continue after the configured check; when
removal or its directory-sync durability cannot be confirmed, the write MUST
report a distinct durability failure and startup MUST run the configured check
when enabled, then abort before migrations or serving. A failed startup MUST
leave a durable `running` marker where the sidecar can be written.

Before reading the run-state sidecar, startup MUST acquire an exclusive
transaction on a persistent `<db>.runstate.lock` SQLite sentinel. Startup MUST
fail closed when that lifetime lock cannot be acquired, rather than trusting a
`clean` marker while another process may be using the database. The process
MUST hold the lock through its database lifetime and MUST release it only after
the `clean` transition has been attempted. SQLite's transaction semantics MUST
release the lock when the process exits unexpectedly. A `clean` transition MUST
be attempted only while the corresponding lifetime lock is held.

Every state other than a recorded `clean` MUST run the check. A missing
sidecar MUST read as unknown rather than clean, so a first run and an upgrade
from a build that never wrote one both still scan. Sidecar content that cannot be read, cannot be
decoded, or is not recognized MUST also read as unknown, and MUST NOT
propagate an error that aborts startup. A sidecar write that fails MUST
remove the temporary and target entries rather than leave a stale `clean`
behind, and MUST directory-sync that removal. If either removal or its
directory sync cannot be confirmed, the write MUST fail closed to its caller.

The run state MUST be recorded even when the check mode is `off`, so
re-enabling the check cannot trust a state the disabled build never
maintained.

A `clean` record MUST be fenced to the database file it describes. The
recorded state MUST capture enough of the file's identity to detect that it
was replaced, including its device and inode, not only its size and
modification time: a restore that preserves timestamps (`tar -x`, `cp -p`,
`rsync -a`) can reproduce both. A `clean` record MUST read as unknown once
any captured attribute no longer matches. The fence applies only to `clean`;
a `running` record stays readable while the process writes to the store. A
clean identity, the newly persisted running identity, and the current database
identity MUST all be non-null and equal before a clean skip. Startup MUST
revalidate the running identity at the final decision seam, so replacement in
any window forces the integrity check.

Run-state transitions MUST be durable. The system MUST sync both the record's
contents and the directory entry that names it, so a power loss cannot retain
an earlier `clean` record while losing the `running` transition that replaced
it. Every directory-sync failure MUST be treated as a failed write, including
a directory that cannot be opened, so storage that cannot confirm durability
leaves no trusted record. The one exception is a platform that offers no
directory handle at all, where the sync MUST be skipped and reported as
success, because rename durability there is the platform's guarantee and
failing closed would prevent those deployments from ever recording a clean
shutdown. That exception MUST be decided by platform rather than by the error
the open reports, because Windows refuses a directory handle with the same
`EACCES` an ordinary permission denial uses. The file fence cannot substitute for this: in WAL mode the main database
file can keep its size and modification time across a long run, so a lost
transition would leave a `clean` record that still matches.

If the directory sync fails after a sidecar replacement, cleanup MUST remove
the temporary and target entries and MUST sync the parent directory again so
the removal itself is durable. A failure of that second sync MUST remain
fail-closed.

Each run-state write MUST create its temporary file with an exclusive,
unpredictable name in the target directory before writing, syncing, and
atomically replacing the sidecar. A predictable temporary pathname MUST NOT be
opened for writing.

Recording `clean` MUST NOT be reachable unless the database engines actually
finished disposing, all reclaimed SQLite teardown work finished within the
bounded shutdown drain, and every database-owning shutdown drain completed.
The database-owning drains include HTTP bridge durable-session marking and
closure, scheduler leader-release, final proxy persistence, and detached
audit/fleet control-plane work. A cancelled or failed disposal, or a drain
that abandons pending work at its deadline, MUST leave the run state unclean.

The configured check mode (`quick`, `full`, `off`) keeps its meaning: this
requirement governs only whether the selected mode runs on a given startup.

#### Scenario: A clean shutdown skips the next scan

- **GIVEN** a SQLite store whose sidecar records a clean shutdown
- **WHEN** the application starts with the check mode enabled
- **THEN** no integrity check runs
- **AND** the sidecar is updated to record that a process is running

#### Scenario: An unrecordable running transition cannot skip the check

- **GIVEN** a SQLite sidecar records `clean`
- **AND** writing the current `running` transition fails
- **WHEN** startup reaches the integrity-check decision
- **THEN** the configured check runs
- **AND** startup does not trust the prior `clean` record

#### Scenario: An unconfirmed running invalidation aborts startup

- **GIVEN** a SQLite sidecar records `clean`
- **AND** replacing the sidecar fails and removal or its directory sync cannot
  be confirmed
- **WHEN** startup reaches the integrity-check decision
- **THEN** the configured check runs when enabled
- **AND** startup aborts before migrations or serving
- **AND** the prior `clean` record is never trusted

#### Scenario: A replacement around startup fencing still scans

- **GIVEN** a SQLite sidecar records `clean` for database identity A
- **WHEN** the database is replaced with identity B before or after the
  `running` transition, or before the final skip decision
- **THEN** the configured integrity check runs against identity B

#### Scenario: A second process cannot trust or replace a live process's clean marker

- **GIVEN** one process holds the `<db>.runstate.lock` lifetime lock
- **WHEN** another process starts against the same SQLite file
- **THEN** startup fails closed before it reads the clean marker
- **AND** the second process does not run migrations or serve traffic

#### Scenario: Process death releases the lifetime lock

- **GIVEN** one process holds the `<db>.runstate.lock` lifetime lock
- **WHEN** that process exits unexpectedly
- **THEN** SQLite releases the transaction while the persistent sentinel file
  remains
- **AND** a subsequent process can acquire the lock before reading the sidecar

#### Scenario: Clean shutdown releases ownership after the marker transition

- **GIVEN** a process holds the `<db>.runstate.lock` lifetime lock
- **WHEN** database disposal completes and shutdown records `clean`
- **THEN** the sidecar records `clean` only while that lock is held
- **AND** the lock is released after the write attempt

#### Scenario: An unfinished previous process still scans

- **GIVEN** a SQLite store whose sidecar records that a process was running
- **WHEN** the application starts with the check mode enabled
- **THEN** the configured integrity check runs

#### Scenario: A missing sidecar still scans

- **GIVEN** an existing SQLite store with no sidecar, as after an upgrade from
  a build that never wrote one
- **WHEN** the application starts with the check mode enabled
- **THEN** the configured integrity check runs

#### Scenario: A disabled check still records the run state

- **GIVEN** a check mode of `off`
- **WHEN** the application starts
- **THEN** no integrity check runs
- **AND** the sidecar records that a process is running, so a later startup
  with the check enabled does not trust the earlier clean record

#### Scenario: A restored database still scans

- **GIVEN** a SQLite store whose sidecar records a clean shutdown
- **WHEN** the database file is replaced from a backup, leaving the sidecar
  in place, and the restore reproduces the recorded size and modification
  time
- **THEN** the clean record reads as unknown
- **AND** the configured integrity check runs against the restored file

#### Scenario: Unverifiable durability leaves no trusted record

- **GIVEN** storage whose directory sync fails
- **WHEN** the system records a run-state transition
- **THEN** the write reports failure and no sidecar remains
- **AND** the next startup runs the integrity check

#### Scenario: A directory that cannot be opened fails the write closed

- **GIVEN** a platform that supports directory handles
- **AND** a run-state directory the process cannot open
- **WHEN** the system records a run-state transition
- **THEN** the write reports failure and no sidecar remains

#### Scenario: Corrupt sidecar content does not abort startup

- **GIVEN** a sidecar whose bytes are not valid UTF-8
- **WHEN** the application starts
- **THEN** the run state reads as unknown
- **AND** the configured integrity check runs instead of startup failing

#### Scenario: A failed disposal is not recorded as clean

- **GIVEN** a shutdown in which disposing the database engines raises or is
  cancelled
- **WHEN** the lifespan teardown completes
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the integrity check

#### Scenario: An abandoned SQLite teardown is not recorded as clean

- **GIVEN** reclaimed SQLite teardown work remains pending when the bounded
  shutdown drain reaches its deadline
- **WHEN** database disposal finishes
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the integrity check

#### Scenario: An abandoned database-owning drain is not recorded as clean

- **GIVEN** the final proxy persistence drain, detached audit/fleet
  control-plane drain, or scheduler leader-release drain leaves database-using
  work pending at its deadline
- **WHEN** database disposal finishes
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the integrity check

#### Scenario: An incomplete HTTP bridge shutdown is not recorded as clean

- **GIVEN** HTTP bridge durable-session marking or closure fails, or its
  background cleanup drain fails, is cancelled, or times out, during shutdown
- **WHEN** database disposal finishes
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the integrity check

#### Scenario: A failed check leaves the state unclean

- **GIVEN** a SQLite store that fails its startup integrity check
- **WHEN** startup aborts with the corruption error
- **THEN** the sidecar does not record a clean shutdown
- **AND** the next startup runs the check again

### Requirement: The SQLite startup integrity check is observable

When the startup integrity check runs, the system MUST log that it is
starting, including the database path, the check mode, and the file size, and
MUST log the elapsed duration when the check passes. A multi-minute scan MUST
NOT present as an unexplained stall with the listener unbound.

#### Scenario: A long scan is attributable

- **GIVEN** a SQLite store large enough for the check to take minutes
- **WHEN** the application starts with the check mode enabled
- **THEN** a log record precedes the scan naming the path, mode, and size
- **AND** a log record on success reports how long the scan took

#### Scenario: A skipped scan says so

- **GIVEN** a SQLite store whose sidecar records a clean shutdown
- **WHEN** the application starts with the check mode enabled
- **THEN** a log record states that the check was skipped after a clean shutdown

### Requirement: Dashboard usage aggregation reads are index-only on PostgreSQL

PostgreSQL deployments MUST maintain a covering partial index (`idx_logs_dash_usage_covering`) on `request_logs (requested_at)` that includes every column referenced by the quota-planner slot aggregation (`account_id`, `api_key_id`, `model`, `reasoning_effort`, `request_kind`, `status`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, `cost_usd`, `id`) and is filtered to `deleted_at IS NULL`, so the aggregation can be satisfied without heap access. SQLite deployments MUST maintain the same index as a partial index on `requested_at`.

#### Scenario: Slot aggregation avoids heap churn

- **GIVEN** the database backend is PostgreSQL
- **AND** `request_logs` contains live (non-deleted) rows in the requested timeframe
- **WHEN** the dashboard requests the usage slot aggregation for any timeframe
- **THEN** PostgreSQL MUST be able to satisfy the time-range filter and every aggregated column from `idx_logs_dash_usage_covering` alone
- **AND** the aggregation result MUST remain semantically identical to the previous heap-backed plan

#### Scenario: Migration is safe after a live hotfix

- **GIVEN** `idx_logs_dash_usage_covering` or `ix_additional_usage_distinct_labels` was already created manually as a live hotfix
- **WHEN** the schema migration is applied
- **THEN** the migration MUST complete without failing on duplicate index creation

#### Scenario: Interrupted concurrent build is repaired, not accepted

- **GIVEN** the database backend is PostgreSQL
- **AND** a previous `CREATE INDEX CONCURRENTLY` for `idx_logs_dash_usage_covering` or `ix_additional_usage_distinct_labels` was interrupted, leaving an invalid index (`pg_index.indisvalid = false`) under the same name
- **WHEN** the schema migration is applied
- **THEN** the migration MUST drop the invalid index and rebuild it rather than accepting it via `IF NOT EXISTS`

### Requirement: Distinct quota-label lookups are index-only

Deployments MUST maintain a composite index (`ix_additional_usage_distinct_labels`) on `additional_usage_history (account_id, quota_key, limit_name, metered_feature)` so the recurring distinct quota-label lookup does not scan the table heap.

#### Scenario: Quota-label poll uses the composite index

- **GIVEN** `additional_usage_history` contains usage rows for one or more accounts
- **WHEN** the dashboard polls for the distinct `(quota_key, limit_name, metered_feature)` labels of a set of accounts
- **THEN** the lookup MUST be satisfiable from `ix_additional_usage_distinct_labels` without reading table rows

### Requirement: Insert-heavy dashboard tables keep autovacuum effective on PostgreSQL

PostgreSQL deployments MUST set per-table autovacuum storage parameters on `request_logs` and `additional_usage_history` (`autovacuum_vacuum_insert_scale_factor = 0.02`, `autovacuum_vacuum_insert_threshold = 50000`, `autovacuum_analyze_scale_factor = 0.02`) so that visibility-map freshness and planner statistics recover promptly even after crash recovery resets the cumulative statistics counters.

#### Scenario: Autovacuum triggers after bounded insert volume

- **GIVEN** the database backend is PostgreSQL
- **AND** the cumulative statistics counters were recently reset (for example by crash recovery)
- **WHEN** inserts accumulate on `request_logs` or `additional_usage_history`
- **THEN** autovacuum MUST become eligible for the table after the configured insert threshold instead of the global default scale factor

### Requirement: Redundant request-log indexes are not maintained

The schema MUST NOT maintain indexes on `request_logs` or `additional_usage_history` whose read paths are fully served by another maintained index on the same table. Specifically, `idx_logs_requested_at`, `idx_logs_api_key_time_account`, and `ix_additional_usage_history_account_id` are dropped; their read paths are served by `idx_logs_requested_at_id`, `idx_logs_api_key_time`, and `ix_additional_usage_distinct_labels` respectively. `idx_logs_request_status_api_key_time` MUST be kept: the sessionless response-owner fallback lookup orders by `requested_at DESC, id DESC` after an equality prefix, and the session-scoped index (`idx_logs_request_status_api_key_session_time`) cannot return that order because `session_id` precedes the ordering columns. On PostgreSQL the redundant indexes MUST be dropped with `DROP INDEX CONCURRENTLY` so writers are not queued behind an `ACCESS EXCLUSIVE` lock during startup migration.

#### Scenario: Reads previously served by a dropped index use its wider twin

- **WHEN** a query filters or orders by the leading columns of a dropped redundant index
- **THEN** the query MUST be satisfiable by the wider index that shares the same leading key columns
- **AND** query results MUST remain semantically identical

#### Scenario: Sessionless response-owner fallback keeps ordered retrieval

- **WHEN** the response-owner lookup falls back to a sessionless search by `request_id` and `status`
- **THEN** the newest matching row by `requested_at DESC, id DESC` MUST be retrievable from `idx_logs_request_status_api_key_time` in index order

