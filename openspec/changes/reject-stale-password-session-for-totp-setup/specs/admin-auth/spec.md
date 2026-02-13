## CHANGED Requirements

### Requirement: TOTP setup requires password session

The system SHALL require a valid password-authenticated session (not the `X-Codex-LB-Setup-Token` header) for TOTP setup and disable operations. The `CODEX_LB_DASHBOARD_SETUP_TOKEN` environment variable and `X-Codex-LB-Setup-Token` header validation MUST be removed.

A `pw=true` session cookie MUST be considered valid for TOTP setup only while password mode is currently active (`password_hash` is set).

#### Scenario: TOTP setup with valid password session

- **WHEN** admin has a valid session with `pw=true` and calls `POST /api/dashboard-auth/totp/setup/start`
- **AND** `password_hash` is set
- **THEN** the system generates a TOTP secret and returns the QR code

#### Scenario: TOTP setup without session

- **WHEN** no valid session exists and `POST /api/dashboard-auth/totp/setup/start` is called
- **THEN** the middleware returns 401 (blocked before reaching the endpoint)

#### Scenario: Stale password session after password removal

- **WHEN** an old session cookie with `pw=true` is replayed after `password_hash` becomes NULL
- **THEN** `POST /api/dashboard-auth/totp/setup/start` and `POST /api/dashboard-auth/totp/setup/confirm` both return 401 with error code `authentication_required`
