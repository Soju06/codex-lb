# admin-auth Specification

## Purpose

Define dashboard authentication behavior so login, bootstrap, TOTP, and session handling stay secure and predictable.

## Requirements
### Requirement: Password setup

The system SHALL allow the admin to set a password from the settings page when no password is currently configured. The password MUST be hashed with bcrypt before storage. Setting a password SHALL transition the system from unauthenticated mode to password-protected mode. When the request is positively identified as originating from the host OS network, first-time setup MAY proceed without a bootstrap token. When the request is non-local, first-time setup MUST require a valid `bootstrapToken` before the password is stored.

#### Scenario: First-time local password setup

- **WHEN** no password is configured (`password_hash` is NULL)
- **AND** the password setup request comes from a local or host-OS client
- **AND** admin submits `POST /api/dashboard-auth/password/setup` with `{ "password": "..." }`
- **THEN** the system stores a bcrypt hash in `DashboardSettings.password_hash`
- **AND** returns a session cookie with `pw=true`

#### Scenario: First-time remote password setup with bootstrap token

- **WHEN** no password is configured (`password_hash` is NULL)
- **AND** the password setup request comes from a non-local client
- **AND** admin submits `POST /api/dashboard-auth/password/setup` with `{ "password": "...", "bootstrapToken": "<valid token>" }`
- **THEN** the system stores a bcrypt hash in `DashboardSettings.password_hash`
- **AND** returns a session cookie with `pw=true`

#### Scenario: First-time remote password setup rejected without valid bootstrap token

- **WHEN** no password is configured (`password_hash` is NULL)
- **AND** the password setup request comes from a non-local client
- **AND** admin submits `POST /api/dashboard-auth/password/setup` without a valid `bootstrapToken`
- **THEN** the system rejects the request with a bootstrap-related authentication error
- **AND** it does not store the password hash

#### Scenario: Trusted-header mode blocks remote fallback password setup without proxy auth

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** no password is configured
- **AND** a request to `POST /api/dashboard-auth/password/setup` does not contain a valid trusted proxy identity
- **THEN** the system returns 401 with `proxy_auth_required`

#### Scenario: Disabled mode rejects password setup

- **WHEN** `dashboard_auth_mode=disabled`
- **AND** `POST /api/dashboard-auth/password/setup` is submitted
- **THEN** the system returns 400 with `password_management_disabled`

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

#### Scenario: Password fallback login works in trusted-header mode

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** a fallback password is configured
- **AND** the request does not contain a trusted proxy identity
- **AND** valid password credentials are submitted
- **THEN** the system returns a valid dashboard session

#### Scenario: Disabled mode rejects password login

- **WHEN** `dashboard_auth_mode=disabled`
- **AND** `POST /api/dashboard-auth/password/login` is submitted
- **THEN** the system returns 400 with `password_management_disabled`

### Requirement: Password change

The system SHALL allow the admin to change the password via `POST /api/dashboard-auth/password/change` by providing both the current password and the new password. The request MUST be authenticated with a valid session. When `totp_required_on_login` is true, the session MUST include `pw=true` and `tv=true`.

#### Scenario: Successful password change

- **WHEN** admin submits `{ "current_password": "old", "new_password": "new" }` with a valid session
- **THEN** the system verifies the current password, stores the new bcrypt hash, and returns 200

#### Scenario: Current password mismatch

- **WHEN** admin submits an incorrect `current_password`
- **THEN** the system returns 401 with error code `invalid_credentials`

#### Scenario: Password change blocked until TOTP verification

- **WHEN** `totp_required_on_login` is true and the session has `pw=true, tv=false`
- **THEN** `POST /api/dashboard-auth/password/change` returns 401 with error code `totp_required`

### Requirement: Password removal

The system SHALL allow the admin to remove the password via `DELETE /api/dashboard-auth/password` by providing the current password in the request body. Removing the password MUST also disable TOTP (`totp_required_on_login = false`) and clear the TOTP secret to return the system to unauthenticated mode. When `totp_required_on_login` is true, the session MUST include `pw=true` and `tv=true`.

#### Scenario: Successful password removal

- **WHEN** admin submits `{ "password": "current" }` to `DELETE /api/dashboard-auth/password` with a valid session
- **THEN** the system sets `password_hash = NULL`, `totp_required_on_login = false`, `totp_secret_encrypted = NULL`, clears the session cookie, and returns 200

#### Scenario: Password removal with invalid password

- **WHEN** admin submits an incorrect password for removal
- **THEN** the system returns 401 with error code `invalid_credentials`

#### Scenario: Password removal blocked until TOTP verification

- **WHEN** `totp_required_on_login` is true and the session has `pw=true, tv=false`
- **THEN** `DELETE /api/dashboard-auth/password` returns 401 with error code `totp_required`

### Requirement: Session authentication guard

The system SHALL enforce session authentication on `/api/*` routes except `/api/dashboard-auth/*`. Authentication SHALL be enforced via a router-level dependency guard, not ASGI middleware.

Authentication required condition: the system SHALL evaluate `password_hash` and `totp_required_on_login` together to determine whether authentication is required. When `password_hash` is NULL **and** `totp_required_on_login` is false, the guard MUST allow all requests (unauthenticated mode). When either `password_hash` is set **or** `totp_required_on_login` is true, the guard MUST require a valid session.

When `insecure_allow_remote_no_auth=true` and the request is positively identified as originating from the host OS network, the guard MAY bypass session enforcement for non-`/api/dashboard-auth/*` routes only while dashboard auth is otherwise disabled. Host-header spoofing alone MUST NOT qualify a request for this bypass.

The system SHALL support an environment-configured dashboard auth mode with values `standard`, `trusted_header`, and `disabled`.

- In `standard` mode, password/TOTP guard semantics remain unchanged.
- In `trusted_header` mode, a trusted reverse-proxy header MAY satisfy dashboard authentication for `/api/*` routes except `/api/dashboard-auth/*`, but only when the request originates from a configured trusted proxy source and `firewall_trust_proxy_headers=true`.
- In `disabled` mode, the dashboard session guard SHALL bypass app-level dashboard auth entirely.

Session validation steps when `requires_auth` is true:
1. A valid session cookie MUST be present (otherwise 401)
2. If `password_hash` is not NULL, the session MUST have `password_verified=true`
3. If `totp_required_on_login` is true, the session MUST have `totp_verified=true`

Migration inconsistency (`password_hash=NULL` with `totp_required_on_login=true`) SHALL always be treated as fail-closed — the system MUST NOT fall back to unauthenticated mode. The system SHOULD emit a warning log/metric for this inconsistency state.

The guard SHALL raise a domain exception on authentication failure. The exception handler SHALL format the response using the dashboard error envelope.

`GET /api/codex/usage` is an exception path for dashboard session auth: the system SHALL require a dedicated caller-identity dependency instead of the dashboard session guard. When the request includes `chatgpt-account-id`, the system SHALL validate the provided bearer token against that active ChatGPT account. When the request omits `chatgpt-account-id`, the same endpoint MAY satisfy caller identity with a valid codex-lb API key. A valid dashboard session cookie alone MUST NOT satisfy this path.

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

#### Scenario: Codex usage accepts valid API key without chatgpt-account-id

- **WHEN** `GET /api/codex/usage` is requested
- **AND** `Authorization` contains a valid codex-lb API key
- **AND** `chatgpt-account-id` is absent
- **THEN** the dedicated caller-identity dependency accepts the request
- **AND** the dashboard session guard does not run for that path

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

#### Scenario: Host-OS request bypasses dashboard guard when auth is disabled

- **WHEN** `password_hash` is NULL and `totp_required_on_login` is false
- **AND** `insecure_allow_remote_no_auth=true`
- **AND** the request is classified as a host-OS request
- **THEN** non-`/api/dashboard-auth/*` dashboard routes bypass session auth

#### Scenario: Spoofed localhost host header does not bypass dashboard guard

- **WHEN** `insecure_allow_remote_no_auth=true`
- **AND** a remote public client sends `Host: localhost`
- **AND** the request does not otherwise prove host-network origin
- **THEN** the dashboard guard does not bypass authentication or remote bootstrap checks

#### Scenario: Host-OS bypass stops once password or TOTP auth is required

- **WHEN** `insecure_allow_remote_no_auth=true`
- **AND** the request is classified as a host-OS request
- **AND** `password_hash` is set or `totp_required_on_login` is true
- **THEN** the dashboard guard requires a valid session

#### Scenario: Trusted header grants dashboard access

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** `firewall_trust_proxy_headers=true`
- **AND** the request socket source is inside `firewall_trusted_proxy_cidrs`
- **AND** the configured trusted header contains a non-empty user identity
- **THEN** the dashboard guard allows the request without requiring a dashboard session cookie

#### Scenario: Trusted header mode fails closed without proxy identity or fallback password

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** no password is configured
- **AND** the request does not contain a valid trusted proxy identity
- **THEN** the dashboard guard returns 401 with `proxy_auth_required`

#### Scenario: Trusted header mode falls back to password auth when configured

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** a password is configured
- **AND** the request does not contain a valid trusted proxy identity
- **THEN** the dashboard guard uses the normal dashboard session validation path

#### Scenario: Disabled mode bypasses dashboard auth

- **WHEN** `dashboard_auth_mode=disabled`
- **THEN** the dashboard guard allows dashboard routes without a password or TOTP session

### Requirement: Session state endpoint

The system SHALL expose `GET /api/dashboard-auth/session` returning the current authentication state including `password_required` (whether a password is configured), `authenticated` (whether the session is fully valid), `totp_required_on_login`, `totp_configured`, the effective dashboard auth mode, password-management availability, and bootstrap flags used for first-run remote setup.

#### Scenario: No password configured

- **WHEN** `password_hash` is NULL
- **THEN** the response contains `{ "passwordRequired": false, "authenticated": true, "totpRequiredOnLogin": false, "totpConfigured": false }`

#### Scenario: Password set, not logged in

- **WHEN** `password_hash` is set and no valid session cookie exists
- **THEN** the response contains `{ "passwordRequired": true, "authenticated": false, ... }`

#### Scenario: Logged in, TOTP pending

- **WHEN** session has `pw=true, tv=false` and `totp_required_on_login` is true
- **THEN** the response contains `{ "passwordRequired": true, "authenticated": false, "totpRequiredOnLogin": true, "totpConfigured": true }`

#### Scenario: Remote bootstrap required before first password setup

- **WHEN** `password_hash` is NULL, `totp_required_on_login` is false, and the session request comes from a non-local client
- **THEN** the response contains `{ "passwordRequired": false, "authenticated": false, "bootstrapRequired": true }`
- **AND** it exposes whether a bootstrap token is configured for remote setup

#### Scenario: Host-OS request reports authenticated bootstrap-free state

- **WHEN** `password_hash` is NULL and `totp_required_on_login` is false
- **AND** `insecure_allow_remote_no_auth=true`
- **AND** the request is classified as a host-OS request
- **THEN** the response reports `authenticated: true`
- **AND** it reports `bootstrapRequired: false`

#### Scenario: Trusted-header mode exposes reverse-proxy blocker state

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** no password is configured
- **AND** the request does not contain a valid trusted proxy identity
- **THEN** the session response contains `{ "authMode": "trusted_header", "passwordManagementEnabled": true, "authenticated": false, "passwordRequired": false }`

#### Scenario: Trusted-header mode exposes authenticated proxy session state

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** the request contains a valid trusted proxy identity
- **THEN** the session response contains `{ "authMode": "trusted_header", "authenticated": true }`

#### Scenario: Disabled mode exposes bypassed auth state

- **WHEN** `dashboard_auth_mode=disabled`
- **THEN** the session response contains `{ "authMode": "disabled", "authenticated": true, "passwordManagementEnabled": false }`

### Requirement: Frontend login gate reflects dashboard auth mode

The SPA SHALL use `authMode` and `passwordManagementEnabled` from the session response to distinguish between password login, trusted reverse-proxy login, and fully disabled dashboard auth.

#### Scenario: Reverse-proxy blocker is shown when trusted header is required

- **WHEN** the SPA loads and the session endpoint returns `authMode: trusted_header`, `authenticated: false`, and `passwordRequired: false`
- **THEN** the SPA shows a reverse-proxy-required blocker instead of the dashboard UI or password login form

#### Scenario: Password management controls are hidden when auth is disabled

- **WHEN** the session endpoint returns `authMode: disabled` and `passwordManagementEnabled: false`
- **THEN** the settings UI hides password/TOTP management controls and shows an explanatory notice

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

### Requirement: Settings cache for auth guard

The system SHALL cache `DashboardSettings` in memory with a TTL of 5 seconds to avoid per-request DB queries in the auth guard. The cache MUST be invalidated immediately when settings are modified via the settings API or password/TOTP management endpoints.

#### Scenario: Cached settings served

- **WHEN** the auth guard runs within 5 seconds of the last cache load
- **THEN** the cached settings are used without a DB query

#### Scenario: Cache invalidation on password setup

- **WHEN** a password is set via `POST /api/dashboard-auth/password/setup`
- **THEN** the settings cache is immediately invalidated so subsequent requests see the new state

### Requirement: TOTP implementation uses pyotp

The system SHALL use the `pyotp` library for TOTP generation and verification, replacing the custom implementation in `app/core/auth/totp.py`. The public interface (`generate_totp_secret`, `verify_totp_code`, `build_otpauth_uri`, `TotpVerificationResult`) MUST be preserved.

#### Scenario: TOTP code verification with pyotp

- **WHEN** a 6-digit TOTP code is submitted for verification
- **THEN** the system validates using `pyotp.TOTP` with the same parameters (SHA1, 6 digits, 30s period, window=1) and replay protection

### Requirement: Bootstrap token lifecycle is visible to operators

When dashboard auth is unconfigured and remote bootstrap is possible, the system MUST maintain a persisted bootstrap-token lifecycle that operators can recover and clients can reason about. The session endpoint MUST expose whether remote bootstrap is required and whether a bootstrap token is currently configured for non-local setup. When the runtime auto-generates the initial bootstrap token, it MUST log that token at warning level so operators can recover it from startup logs.

#### Scenario: Remote session response exposes bootstrap-token state

- **WHEN** dashboard auth is unconfigured and a non-local client requests `GET /api/dashboard-auth/session`
- **THEN** the response includes `bootstrapRequired: true`
- **AND** it includes `bootstrapTokenConfigured` indicating whether a bootstrap token is currently available

#### Scenario: Auto-generated bootstrap token is logged at warning level

- **WHEN** the service starts with dashboard auth unconfigured and no active bootstrap token
- **THEN** the runtime creates or persists an auto-generated bootstrap token
- **AND** it logs that token at warning level for operator retrieval

### Requirement: Login rate limiting

The system SHALL rate-limit failed password login attempts using the existing `TotpRateLimiter` pattern: maximum 8 failures per 60-second window. On rate limit breach, the system MUST return 429 with a `Retry-After` header. Requests rejected because password login is not configured MUST NOT consume that failed-login budget.

#### Scenario: Rate limit triggered

- **WHEN** 8 failed login attempts occur within 60 seconds
- **THEN** the 9th attempt returns 429 with `Retry-After` header indicating seconds until the window resets

#### Scenario: Rate limit resets on success

- **WHEN** a successful login occurs after failed attempts
- **THEN** the failure counter for that client resets to zero

#### Scenario: Unconfigured password login does not spend rate-limit budget

- **WHEN** no password is configured and a login request is submitted
- **THEN** the system returns `password_not_configured`
- **AND** it does not consume one of the failed-login attempts for that client
