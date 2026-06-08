## ADDED Requirements

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
