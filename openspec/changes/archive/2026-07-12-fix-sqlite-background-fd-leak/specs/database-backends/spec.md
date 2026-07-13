## ADDED Requirements

### Requirement: File-backed SQLite engines do not retain idle pooled descriptors

File-backed SQLite main and background async engines MUST use non-pooled connection semantics.

SQLite `:memory:` databases MUST preserve the existing shared-engine behavior
for background sessions so schema state remains visible to background tasks.

Pool controls (`database_pool_size`, `database_max_overflow`,
`database_background_pool_size`, `database_background_max_overflow`, and
`database_pool_timeout_seconds`) SHALL constrain pooled backends only. They
SHALL NOT be passed to file-backed SQLite engines.

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

## MODIFIED Requirements

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
- **AND** the background engine pool (`database_background_pool_size` + `database_background_max_overflow`) is not driven to exhaustion by stranded refresh connections
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
