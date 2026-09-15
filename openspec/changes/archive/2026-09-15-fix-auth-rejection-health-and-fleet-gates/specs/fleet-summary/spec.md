## MODIFIED Requirements

### Requirement: Fleet refresh requests existing usage refresh policy

The system SHALL expose `POST /api/fleet/refresh` for trusted local fleet consumers. The route MUST require a valid Bearer API key even when global proxy API-key authentication is disabled. The route MUST request a usage refresh through codex-lb's existing usage refresh machinery and MUST NOT refresh inside proxy account selection.

The route MUST preserve existing usage-refresh rules for disabled refresh, fresh samples, auth cooldowns, paused accounts, and deactivated accounts. Refresh-only warnings with usable or unknown-expiry access credentials MUST remain eligible and included in `attemptedCount`. Reauthentication accounts with known expired or proven rejected access credentials MUST be excluded from attempts. This MUST NOT authorize refresh-token exchange for warning accounts.

#### Scenario: Fleet refresh returns minimal outcome

- **WHEN** a valid client calls `POST /api/fleet/refresh`
- **THEN** the response includes `ok: true`, `usageWritten`, `accountCount`, `attemptedCount`, and `generatedAt`
- **AND** the response does not include account credentials or token material

#### Scenario: Fleet refresh skips unsafe account states

- **GIVEN** active and paused accounts exist
- **WHEN** a valid client calls `POST /api/fleet/refresh`
- **THEN** active accounts are eligible for the refresh attempt
- **AND** paused, deactivated, and reauthentication accounts with expired or proven rejected access credentials are not attempted

#### Scenario: Fleet refresh includes usable refresh-only warnings

- **WHEN** a valid client requests fleet refresh with refresh-only warning accounts whose access credentials are usable or have unknown expiry
- **THEN** those accounts MUST be included in `attemptedCount` and passed to the existing usage refresh policy
