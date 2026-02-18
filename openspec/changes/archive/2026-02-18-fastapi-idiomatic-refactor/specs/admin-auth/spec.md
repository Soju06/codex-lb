## MODIFIED Requirements

### Requirement: Session authentication guard

The system SHALL enforce session authentication on `/api/*` routes except `/api/dashboard-auth/*`. Authentication SHALL be enforced via a router-level dependency guard, not ASGI middleware.

Authentication required condition: the system SHALL evaluate `password_hash` and `totp_required_on_login` together to determine whether authentication is required. When `password_hash` is NULL **and** `totp_required_on_login` is false, the guard MUST allow all requests (unauthenticated mode). When either `password_hash` is set **or** `totp_required_on_login` is true, the guard MUST require a valid session.

Session validation steps when `requires_auth` is true:
1. A valid session cookie MUST be present (otherwise 401)
2. If `password_hash` is not NULL, the session MUST have `password_verified=true`
3. If `totp_required_on_login` is true, the session MUST have `totp_verified=true`

Migration inconsistency (`password_hash=NULL` with `totp_required_on_login=true`) SHALL always be treated as fail-closed — the system MUST NOT fall back to unauthenticated mode. The system SHOULD emit a warning log/metric for this inconsistency state.

The guard SHALL raise a domain exception on authentication failure. The exception handler SHALL format the response using the dashboard error envelope.

`GET /api/codex/usage` is an exception path for dashboard session auth: the system SHALL require a valid Codex bearer caller identity (`Authorization: Bearer <token>` + `chatgpt-account-id`) via a dedicated dependency, not the dashboard session guard.

#### Scenario: Codex usage caller identity validation in password mode

- **WHEN** `password_hash` is set and `GET /api/codex/usage` is requested
- **AND** `Authorization` bearer token and `chatgpt-account-id` are provided
- **AND** `chatgpt-account-id` exists in LB accounts
- **AND** upstream usage validation succeeds for the token/account pair
- **THEN** the guard allows the request

#### Scenario: Codex usage caller identity required even with dashboard session

- **WHEN** `password_hash` is set and `GET /api/codex/usage` is requested with a valid dashboard session cookie
- **AND** codex bearer caller identity is missing
- **THEN** the guard returns 401

#### Scenario: Codex usage denied when caller identity is not authorized

- **WHEN** `GET /api/codex/usage` is requested
- **AND** codex bearer caller identity is missing or invalid
- **THEN** the guard returns 401

#### Scenario: Legacy TOTP protection preserved when password_hash is NULL

- **WHEN** `password_hash` is NULL and `totp_required_on_login` is true
- **AND** no session cookie is present
- **THEN** the guard returns 401

#### Scenario: TOTP-only session accepted when password is not configured

- **WHEN** `password_hash` is NULL and `totp_required_on_login` is true
- **AND** session has `password_verified=false` and `totp_verified=true`
- **THEN** the guard allows the request

#### Scenario: TOTP verification required even with password session

- **WHEN** `password_hash` is NULL and `totp_required_on_login` is true
- **AND** session has `password_verified=true` but `totp_verified=false`
- **THEN** the guard returns 401 with `totp_required` indication

### Requirement: Settings cache for auth guard

The system SHALL cache `DashboardSettings` in memory with a TTL of 5 seconds to avoid per-request DB queries in the auth guard. The cache MUST be invalidated immediately when settings are modified via the settings API or password/TOTP management endpoints.

#### Scenario: Cached settings served

- **WHEN** the auth guard runs within 5 seconds of the last cache load
- **THEN** the cached settings are used without a DB query

#### Scenario: Cache invalidation on password setup

- **WHEN** a password is set via `POST /api/dashboard-auth/password/setup`
- **THEN** the settings cache is immediately invalidated so subsequent requests see the new state

## RENAMED Requirements

### Requirement: Session authentication middleware
- **FROM:** Session authentication middleware
- **TO:** Session authentication guard

### Requirement: Settings cache for middleware
- **FROM:** Settings cache for middleware
- **TO:** Settings cache for auth guard
