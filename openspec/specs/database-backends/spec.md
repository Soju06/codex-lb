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

Each PostgreSQL statement MUST additionally be bounded by the fixed asyncpg `command_timeout` application constant in `app/db/session.py`, so a query stalled on a half-dead connection surfaces as an error within the bound instead of awaiting indefinitely — pre-ping only protects the first statement after checkout, not a connection that dies mid-statement. A statement that dies mid-flight while its caller holds an application lock MUST therefore release that caller within the bound. Alembic migrations run on their own synchronous engine and are not subject to this bound.

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

- **WHEN** a statement's connection dies after dispatch (network partition, half-dead peer) and no response arrives
- **THEN** asyncpg cancels the statement and raises within the fixed `command_timeout` bound
- **AND** any application lock held by the caller is released within that bound instead of being held indefinitely

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

