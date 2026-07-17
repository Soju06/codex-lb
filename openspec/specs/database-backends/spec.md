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

#### Scenario: Stale connections are rejected before checkout

- **WHEN** a pooled connection has been closed by the server while sitting idle
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy issues a pre-ping (`SELECT 1`), detects the dead connection, and transparently replaces it
- **AND** the application returns `200` (or the real business-level result), not `500 server_error` with `asyncpg.InterfaceError: connection is closed`

#### Scenario: Pool recycle bounds connection age

- **WHEN** a pooled connection has been open longer than the fixed 1800-second recycle window
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy discards and replaces the connection before the next query

#### Scenario: SQLite backends are not affected

- **WHEN** `database_url` resolves to a SQLite backend (file or `:memory:`)
- **THEN** neither `pool_pre_ping` nor `pool_recycle` is configured on the engine
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

