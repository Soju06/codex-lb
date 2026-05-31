## ADDED Requirements

### Requirement: Expired dashboard session returns to login

When the dashboard API client receives a 401 while standard password
authentication is required, the SPA SHALL clear any stale pending-TOTP state
from the auth store before selecting the auth screen. The auth gate SHALL show
the password login form rather than the TOTP verification dialog unless the
current session response or a fresh password-login response explicitly reports
`totpRequiredOnLogin: true`.

#### Scenario: 401 clears stale pending-TOTP state

- **GIVEN** the SPA auth store still has `totpRequiredOnLogin: true` from an earlier password-login step
- **WHEN** a dashboard API request returns 401 because the password-authenticated session is no longer active
- **THEN** the auth store sets `authenticated: false`
- **AND** the auth store sets `totpRequiredOnLogin: false`
- **AND** the auth store sets `passwordSessionActive: false`
- **AND** the auth gate shows the password login form for standard password auth

#### Scenario: Bootstrap state is preserved

- **GIVEN** the SPA auth store indicates first-run bootstrap is required
- **WHEN** a dashboard API request returns 401
- **THEN** the auth store preserves the bootstrap-required state
- **AND** the bootstrap setup screen remains eligible to render
