## MODIFIED Requirements

### Requirement: Session authentication guard

The system SHALL support an env-configured dashboard auth mode with values `standard`, `trusted_header`, and `disabled`.

- In `standard` mode, the existing password/TOTP guard semantics remain unchanged.
- In `trusted_header` mode, a trusted reverse-proxy header MAY satisfy dashboard authentication for `/api/*` routes except `/api/dashboard-auth/*`, but only when the request originates from a configured trusted proxy source and `firewall_trust_proxy_headers=true`.
- In `disabled` mode, the dashboard session guard SHALL bypass app-level dashboard auth entirely.

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

### Requirement: Password setup

The system SHALL continue to allow first-time password setup when no password is configured, except when dashboard auth is delegated to a trusted header or fully disabled.

#### Scenario: Trusted-header mode blocks remote fallback password setup without proxy auth

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** no password is configured
- **AND** a request to `POST /api/dashboard-auth/password/setup` does not contain a valid trusted proxy identity
- **THEN** the system returns 401 with `proxy_auth_required`

#### Scenario: Trusted-header mode allows authenticated fallback password setup

- **WHEN** `dashboard_auth_mode=trusted_header`
- **AND** no password is configured
- **AND** a request to `POST /api/dashboard-auth/password/setup` contains a valid trusted proxy identity
- **THEN** the system stores the password hash and returns session state

#### Scenario: Disabled mode rejects password setup

- **WHEN** `dashboard_auth_mode=disabled`
- **AND** `POST /api/dashboard-auth/password/setup` is submitted
- **THEN** the system returns 400 with `password_management_disabled`

### Requirement: Password login

Password login SHALL remain available as an optional fallback in `trusted_header` mode when a password is configured. Password login SHALL be disabled in `disabled` mode.

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

### Requirement: Session state endpoint

The system SHALL expose the effective dashboard auth mode through `GET /api/dashboard-auth/session` so the SPA can render the correct login or blocker state.

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

### Requirement: Frontend login gate

The SPA SHALL use `authMode` and `passwordManagementEnabled` from the session response to distinguish between password login, trusted reverse-proxy login, and fully disabled dashboard auth.

#### Scenario: Reverse-proxy blocker is shown when trusted header is required

- **WHEN** the SPA loads and the session endpoint returns `authMode: trusted_header`, `authenticated: false`, and `passwordRequired: false`
- **THEN** the SPA shows a reverse-proxy-required blocker instead of the dashboard UI or password login form

#### Scenario: Password management controls are hidden when auth is disabled

- **WHEN** the session endpoint returns `authMode: disabled` and `passwordManagementEnabled: false`
- **THEN** the settings UI hides password/TOTP management controls and shows an explanatory notice
