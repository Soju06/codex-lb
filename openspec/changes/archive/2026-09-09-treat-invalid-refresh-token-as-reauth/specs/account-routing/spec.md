## ADDED Requirements

### Requirement: Invalid refresh tokens require account re-authentication

The system MUST classify an upstream OAuth `invalid_refresh_token` refresh
failure as permanent, persist the affected account as re-authentication required
through the guarded refresh-account status path, and exclude that account from
normal account selection until an operator reauthenticates or imports fresh
credentials.

#### Scenario: OAuth invalid-refresh-token response removes the account from routing

- **GIVEN** an active account attempts a token refresh
- **WHEN** upstream OAuth returns `invalid_refresh_token`
- **THEN** the refresh path persists the account status as `reauth_required`
- **AND** the account is not selected for subsequent routed requests until fresh
  credentials are supplied
