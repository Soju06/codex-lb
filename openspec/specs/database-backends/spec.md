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

When `database_url` resolves to a PostgreSQL backend, the application MUST configure each async engine — both the request-path `engine` and the optional background-task `_background_engine` — with `pool_pre_ping=True` and a finite `pool_recycle` window. This is required so the application detects connections that the PostgreSQL server has silently closed (idle timeout, restart, network reset) before the first real query is dispatched on them, and so connections are cycled before they reach any reasonable upstream keep-alive boundary.

#### Scenario: Stale connections are rejected before checkout

- **WHEN** a pooled connection has been closed by the server while sitting idle
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy issues a pre-ping (`SELECT 1`), detects the dead connection, and transparently replaces it
- **AND** the application returns `200` (or the real business-level result), not `500 server_error` with `asyncpg.InterfaceError: connection is closed`

#### Scenario: Pool recycle bounds connection age

- **WHEN** a pooled connection has been open longer than `database_pool_recycle_seconds`
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy discards and replaces the connection before the next query
- **AND** the default `database_pool_recycle_seconds` is `1800` seconds

#### Scenario: SQLite backends are not affected

- **WHEN** `database_url` resolves to a SQLite backend (file or `:memory:`)
- **THEN** neither `pool_pre_ping` nor `pool_recycle` is configured on the engine
- **AND** existing SQLite-specific tuning (PRAGMAs, `busy_timeout`) is unchanged

### Requirement: Database pool controls cover request-adjacent background sessions
The service SHALL expose database pool settings for both the main request pool
and the background/request-adjacent session pool. The background pool SHALL
default to the main pool size and overflow settings, and operators MAY override
the background pool size and overflow separately.

#### Scenario: Background pool inherits main pool capacity
- **WHEN** `database_background_pool_size` and `database_background_max_overflow` are unset
- **THEN** the background/request-adjacent DB pool uses `database_pool_size` and `database_max_overflow`

#### Scenario: Background pool has explicit lower capacity
- **WHEN** `database_background_pool_size` and `database_background_max_overflow` are configured
- **THEN** the background/request-adjacent DB pool uses those explicit values

### Requirement: Detached background tasks own their database session lifetime

A background task that is intentionally decoupled from its caller's lifetime — for example a singleflight refresh kept alive with `asyncio.shield` so concurrent waiters share one in-flight operation — MUST NOT perform database work through a session whose lifetime is owned by the (cancellable) caller. Such a task MUST acquire its own session (via `get_background_session()` or an equivalent caller-independent factory), use it, and release it entirely within the task. This prevents the caller's cancellation (e.g. a client disconnect) from closing a session that the still-running detached task then touches, which — because `AsyncSession` is not safe for concurrent use — strands a pooled connection that never returns and, accumulated over time, exhausts the background engine pool.

#### Scenario: Client disconnect during token refresh does not strand a connection

- **GIVEN** a proxy request triggers an account token refresh through `AuthManager.ensure_fresh`
- **AND** the refresh runs as a detached singleflight task held alive by `asyncio.shield`
- **AND** the request that initiated it is bound to a request-scoped background session
- **WHEN** the client disconnects mid-refresh and the request task is cancelled
- **THEN** the refresh task MUST complete its token/status writes against its own session, acquired independently of the cancelled request
- **AND** the request-scoped session MUST close without being used by the refresh task after close
- **AND** no background-pool connection is left checked out after the refresh task finishes

#### Scenario: Non-cancellable callers retain the bound-session path

- **GIVEN** a caller whose session is not tied to a client-cancellable request (for example the usage refresh scheduler holding its own loop-scoped session)
- **AND** that caller invokes `AuthManager.ensure_fresh` without supplying a refresh session factory
- **WHEN** a token refresh runs
- **THEN** the refresh MAY use the caller's bound session
- **AND** behavior is unchanged from before this requirement (no new session is opened)

#### Scenario: Accumulated leak no longer exhausts the background pool

- **GIVEN** repeated client disconnects during token refreshes over an extended period
- **WHEN** each disconnect-during-refresh occurs
- **THEN** each refresh task releases its connection back to the background pool
- **AND** the background engine pool (`database_background_pool_size` + `database_background_max_overflow`) is not driven to exhaustion by stranded refresh connections
- **AND** `/backend-api/codex/*` requests do not begin returning `500` from `QueuePool limit ... connection timed out` as a result of this path


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
