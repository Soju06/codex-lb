## MODIFIED Requirements

### Requirement: Session authentication middleware

The system SHALL enforce session authentication on `/api/*` routes except `/api/dashboard-auth/*`.

Authentication required condition: the system SHALL evaluate `password_hash` and `totp_required_on_login` together to determine whether authentication is required. When `password_hash` is NULL **and** `totp_required_on_login` is false, the middleware MUST allow all requests (unauthenticated mode). When either `password_hash` is set **or** `totp_required_on_login` is true, the middleware MUST require a valid session.

Session validation steps when `requires_auth` is true:
1. A valid session cookie MUST be present (otherwise 401)
2. If `password_hash` is not NULL, the session MUST have `password_verified=true`
3. If `totp_required_on_login` is true, the session MUST have `totp_verified=true`

Migration inconsistency (`password_hash=NULL` with `totp_required_on_login=true`) SHALL always be treated as fail-closed — the system MUST NOT fall back to unauthenticated mode. The system SHOULD emit a warning log/metric for this inconsistency state.

`GET /api/codex/usage` is an exception path for dashboard session auth: the system SHALL require a valid Codex bearer caller identity (`Authorization: Bearer <token>` + `chatgpt-account-id`) that is authorized against LB account membership and successfully validated against upstream usage.

#### Scenario: Codex usage caller identity validation in password mode

- **WHEN** `password_hash` is set and `GET /api/codex/usage` is requested
- **AND** `Authorization` bearer token and `chatgpt-account-id` are provided
- **AND** `chatgpt-account-id` exists in LB accounts
- **AND** upstream usage validation succeeds for the token/account pair
- **THEN** the middleware allows the request

#### Scenario: Codex usage caller identity required even with dashboard session

- **WHEN** `password_hash` is set and `GET /api/codex/usage` is requested with a valid dashboard session cookie
- **AND** codex bearer caller identity is missing
- **THEN** the middleware returns 401

#### Scenario: Codex usage denied when caller identity is not authorized

- **WHEN** `GET /api/codex/usage` is requested
- **AND** codex bearer caller identity is missing or invalid
- **THEN** the middleware returns 401

#### Scenario: Legacy TOTP protection preserved when password_hash is NULL

- **WHEN** `password_hash` is NULL and `totp_required_on_login` is true
- **AND** no session cookie is present
- **THEN** the middleware returns 401

#### Scenario: TOTP-only session accepted when password is not configured

- **WHEN** `password_hash` is NULL and `totp_required_on_login` is true
- **AND** session has `password_verified=false` and `totp_verified=true`
- **THEN** the middleware allows the request

#### Scenario: TOTP verification required even with password session

- **WHEN** `password_hash` is NULL and `totp_required_on_login` is true
- **AND** session has `password_verified=true` but `totp_verified=false`
- **THEN** the middleware returns 401 with `totp_required` indication
