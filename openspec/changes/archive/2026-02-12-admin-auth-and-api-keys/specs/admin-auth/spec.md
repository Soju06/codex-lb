## ADDED Requirements

### Requirement: Password setup

The system SHALL allow the admin to set a password from the settings page when no password is currently configured. The password MUST be hashed with bcrypt before storage. Setting a password SHALL transition the system from unauthenticated mode to password-protected mode.

#### Scenario: First-time password setup

- **WHEN** no password is configured (`password_hash` is NULL) and admin submits `POST /api/dashboard-auth/password/setup` with `{ "password": "..." }`
- **THEN** the system stores a bcrypt hash in `DashboardSettings.password_hash` and returns a session cookie with `pw=true`

#### Scenario: Password setup rejected when already configured

- **WHEN** a password is already configured and admin submits `POST /api/dashboard-auth/password/setup`
- **THEN** the system returns 409 Conflict

#### Scenario: Password setup with weak password

- **WHEN** admin submits a password shorter than 8 characters
- **THEN** the system returns 422 with a validation error

### Requirement: Password login

The system SHALL authenticate the admin via `POST /api/dashboard-auth/password/login` by comparing the submitted password against the stored bcrypt hash. On success, the system MUST issue a Fernet-encrypted session cookie containing `{exp, pw: true, tv: false}`.

#### Scenario: Successful password login

- **WHEN** admin submits a valid password to `POST /api/dashboard-auth/password/login`
- **THEN** the system sets the `codex_lb_dashboard_session` cookie (httponly, secure, samesite=lax, max-age=12h) and returns 200 with session state

#### Scenario: Invalid password

- **WHEN** admin submits an incorrect password to `POST /api/dashboard-auth/password/login`
- **THEN** the system returns 401 with error code `invalid_credentials`

#### Scenario: Login when no password configured

- **WHEN** no password is configured and a login request is submitted
- **THEN** the system returns 400 with error code `password_not_configured`

### Requirement: Password change

The system SHALL allow the admin to change the password via `POST /api/dashboard-auth/password/change` by providing both the current password and the new password. The request MUST be authenticated with a valid session.

#### Scenario: Successful password change

- **WHEN** admin submits `{ "current_password": "old", "new_password": "new" }` with a valid session
- **THEN** the system verifies the current password, stores the new bcrypt hash, and returns 200

#### Scenario: Current password mismatch

- **WHEN** admin submits an incorrect `current_password`
- **THEN** the system returns 401 with error code `invalid_credentials`

### Requirement: Password removal

The system SHALL allow the admin to remove the password via `DELETE /api/dashboard-auth/password` by providing the current password in the request body. Removing the password MUST also disable TOTP (`totp_required_on_login = false`) and clear the TOTP secret to return the system to unauthenticated mode.

#### Scenario: Successful password removal

- **WHEN** admin submits `{ "password": "current" }` to `DELETE /api/dashboard-auth/password` with a valid session
- **THEN** the system sets `password_hash = NULL`, `totp_required_on_login = false`, `totp_secret_encrypted = NULL`, clears the session cookie, and returns 200

#### Scenario: Password removal with invalid password

- **WHEN** admin submits an incorrect password for removal
- **THEN** the system returns 401 with error code `invalid_credentials`

### Requirement: Session authentication middleware

The system SHALL enforce session authentication on all `/api/*` routes except `/api/dashboard-auth/*`. When `password_hash` is NULL, the middleware MUST allow all requests (unauthenticated mode). When `password_hash` is set, the middleware MUST validate the session cookie.

#### Scenario: Unauthenticated mode (no password set)

- **WHEN** `password_hash` is NULL and a request arrives at `/api/accounts`
- **THEN** the middleware allows the request through without checking cookies

#### Scenario: Password set but no session cookie

- **WHEN** `password_hash` is set and a request arrives at `/api/settings` without a session cookie
- **THEN** the middleware returns 401 with error code `authentication_required`

#### Scenario: Valid session with password only

- **WHEN** `password_hash` is set, `totp_required_on_login` is false, and a request has a valid session cookie with `pw=true`
- **THEN** the middleware allows the request through

#### Scenario: Valid session but TOTP not verified

- **WHEN** `password_hash` is set, `totp_required_on_login` is true, and the session cookie has `pw=true, tv=false`
- **THEN** the middleware returns 401 with error code `totp_required`

#### Scenario: Fully authenticated session

- **WHEN** `password_hash` is set, `totp_required_on_login` is true, and the session cookie has `pw=true, tv=true`
- **THEN** the middleware allows the request through

#### Scenario: Auth endpoint exemption

- **WHEN** a request arrives at `/api/dashboard-auth/session` or any `/api/dashboard-auth/*` path
- **THEN** the middleware allows the request through regardless of auth state

### Requirement: Session state endpoint

The system SHALL expose `GET /api/dashboard-auth/session` returning the current authentication state including `password_required` (whether a password is configured), `authenticated` (whether the session is fully valid), `totp_required_on_login`, and `totp_configured`.

#### Scenario: No password configured

- **WHEN** `password_hash` is NULL
- **THEN** the response contains `{ "passwordRequired": false, "authenticated": true, "totpRequiredOnLogin": false, "totpConfigured": false }`

#### Scenario: Password set, not logged in

- **WHEN** `password_hash` is set and no valid session cookie exists
- **THEN** the response contains `{ "passwordRequired": true, "authenticated": false, ... }`

#### Scenario: Logged in, TOTP pending

- **WHEN** session has `pw=true, tv=false` and `totp_required_on_login` is true
- **THEN** the response contains `{ "passwordRequired": true, "authenticated": false, "totpRequiredOnLogin": true, "totpConfigured": true }`

### Requirement: TOTP setup requires password session

The system SHALL require a valid password-authenticated session (not the `X-Codex-LB-Setup-Token` header) for TOTP setup and disable operations. The `CODEX_LB_DASHBOARD_SETUP_TOKEN` environment variable and `X-Codex-LB-Setup-Token` header validation MUST be removed.

#### Scenario: TOTP setup with valid password session

- **WHEN** admin has a valid session with `pw=true` and calls `POST /api/dashboard-auth/totp/setup/start`
- **THEN** the system generates a TOTP secret and returns the QR code

#### Scenario: TOTP setup without session

- **WHEN** no valid session exists and `POST /api/dashboard-auth/totp/setup/start` is called
- **THEN** the middleware returns 401 (blocked before reaching the endpoint)

### Requirement: TOTP verification issues full session

When TOTP verification succeeds via `POST /api/dashboard-auth/totp/verify`, the system MUST upgrade the session cookie to `{pw: true, tv: true}`.

#### Scenario: Successful TOTP verification

- **WHEN** admin submits a valid TOTP code with a `pw=true` session
- **THEN** the system returns a new session cookie with `pw=true, tv=true` and `authenticated: true`

### Requirement: Settings cache for middleware

The system SHALL cache `DashboardSettings` in memory with a TTL of 5 seconds to avoid per-request DB queries in the middleware. The cache MUST be invalidated immediately when settings are modified via the settings API or password/TOTP management endpoints.

#### Scenario: Cached settings served

- **WHEN** the middleware runs within 5 seconds of the last cache load
- **THEN** the cached settings are used without a DB query

#### Scenario: Cache invalidation on password setup

- **WHEN** a password is set via `POST /api/dashboard-auth/password/setup`
- **THEN** the settings cache is immediately invalidated so subsequent requests see the new state

### Requirement: TOTP implementation uses pyotp

The system SHALL use the `pyotp` library for TOTP generation and verification, replacing the custom implementation in `app/core/auth/totp.py`. The public interface (`generate_totp_secret`, `verify_totp_code`, `build_otpauth_uri`, `TotpVerificationResult`) MUST be preserved.

#### Scenario: TOTP code verification with pyotp

- **WHEN** a 6-digit TOTP code is submitted for verification
- **THEN** the system validates using `pyotp.TOTP` with the same parameters (SHA1, 6 digits, 30s period, window=1) and replay protection

### Requirement: Login rate limiting

The system SHALL rate-limit failed password login attempts using the existing `TotpRateLimiter` pattern: maximum 8 failures per 60-second window. On rate limit breach, the system MUST return 429 with a `Retry-After` header.

#### Scenario: Rate limit triggered

- **WHEN** 8 failed login attempts occur within 60 seconds
- **THEN** the 9th attempt returns 429 with `Retry-After` header indicating seconds until the window resets

#### Scenario: Rate limit resets on success

- **WHEN** a successful login occurs after failed attempts
- **THEN** the failure counter for that client resets to zero

### Requirement: Frontend login gate

The SPA SHALL check `GET /api/dashboard-auth/session` on load. When `passwordRequired` is true and `authenticated` is false, the SPA MUST display only the login form (password input, then conditional TOTP input). Dashboard, accounts, and settings tabs MUST be hidden until authenticated. The TOTP input MUST use an HTML dialog, not `window.prompt()`.

#### Scenario: SPA loads with password required

- **WHEN** the SPA loads and the session endpoint returns `passwordRequired: true, authenticated: false`
- **THEN** only the login form is rendered; no dashboard data is visible

#### Scenario: Password login then TOTP required

- **WHEN** the user submits a valid password and `totpRequiredOnLogin` is true
- **THEN** the SPA shows an HTML TOTP input dialog; after valid code submission, the full UI is shown

#### Scenario: No password configured

- **WHEN** the SPA loads and the session endpoint returns `passwordRequired: false`
- **THEN** the full dashboard UI is shown immediately
