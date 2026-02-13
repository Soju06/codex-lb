## CHANGED Requirements

### Requirement: Session authentication middleware

The system SHALL enforce session authentication on `/api/*` routes except `/api/dashboard-auth/*`. When `password_hash` is NULL, the middleware MUST allow all requests (unauthenticated mode). When `password_hash` is set, the middleware MUST validate the session cookie.

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
