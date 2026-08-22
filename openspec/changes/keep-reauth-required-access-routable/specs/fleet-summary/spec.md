## MODIFIED Requirements

### Requirement: Fleet refresh requests existing usage refresh policy

The system SHALL expose `POST /api/fleet/refresh` for trusted local fleet consumers. The route MUST require a valid Bearer API key even when global proxy API-key authentication is disabled. The route MUST request a usage refresh through codex-lb's existing usage refresh machinery and MUST NOT refresh inside proxy account selection.

The route MUST preserve existing usage-refresh rules for disabled refresh, fresh samples, auth cooldowns, paused accounts, request-routable `reauth_required` accounts, and deactivated accounts. A `reauth_required` account MAY use its stored access token for usage refresh but MUST NOT proactively exchange its known-bad refresh token.

#### Scenario: Fleet refresh returns minimal outcome

- **WHEN** a valid client calls `POST /api/fleet/refresh`
- **THEN** the response includes `ok: true`, `usageWritten`, `accountCount`, `attemptedCount`, and `generatedAt`
- **AND** the response does not include account credentials or token material

#### Scenario: Fleet refresh skips unsafe account states

- **GIVEN** active, `reauth_required`, paused, and deactivated accounts exist
- **WHEN** a valid client calls `POST /api/fleet/refresh`
- **THEN** active and `reauth_required` accounts are eligible for the refresh attempt
- **AND** paused and deactivated accounts are not attempted
- **AND** the `reauth_required` attempt does not proactively exchange refresh-token material
