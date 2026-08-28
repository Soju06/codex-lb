## ADDED Requirements

### Requirement: Fleet summary requires API key authentication

The system SHALL expose `GET /api/fleet/summary` for trusted local fleet consumers. The route MUST require a valid Bearer API key even when global proxy API-key authentication is disabled.

#### Scenario: Missing fleet summary key is rejected

- **WHEN** a client calls `GET /api/fleet/summary` without a Bearer token
- **THEN** the system returns 401
- **AND** no account summary payload is returned

#### Scenario: Valid fleet summary key returns account capacity

- **WHEN** a client calls `GET /api/fleet/summary` with a valid Bearer API key
- **THEN** the response includes `accounts[]`
- **AND** each account includes `accountId`, `displayName`, `email`, `status`, `planType`, `primary`, `secondary`, and `lastRefreshAt`
- **AND** each window includes `remainingPercent`, `resetAt`, and `windowMinutes`

### Requirement: Fleet summary excludes sensitive data

Fleet summary responses MUST NOT include OAuth token material, auth token status, raw credit balances, request-cost detail, additional quota detail, or deactivation reasons.

#### Scenario: Sensitive fields are omitted

- **WHEN** a valid client calls `GET /api/fleet/summary`
- **THEN** no response object includes token fields, `auth`, credit-balance fields, request usage, additional quotas, or deactivation reasons

### Requirement: Fleet refresh requests existing usage refresh policy

The system SHALL expose `POST /api/fleet/refresh` for trusted local fleet consumers. The route MUST require a valid Bearer API key even when global proxy API-key authentication is disabled. The route MUST request a usage refresh through codex-lb's existing usage refresh machinery and MUST NOT refresh inside proxy account selection.

The route MUST preserve existing usage-refresh rules for disabled refresh, fresh samples, auth cooldowns, paused accounts, reauth-required accounts, and deactivated accounts.

#### Scenario: Fleet refresh returns minimal outcome

- **WHEN** a valid client calls `POST /api/fleet/refresh`
- **THEN** the response includes `ok: true`, `usageWritten`, `accountCount`, `attemptedCount`, and `generatedAt`
- **AND** the response does not include account credentials or token material

#### Scenario: Fleet refresh skips unsafe account states

- **GIVEN** active and paused accounts exist
- **WHEN** a valid client calls `POST /api/fleet/refresh`
- **THEN** active accounts are eligible for the refresh attempt
- **AND** paused, reauth-required, and deactivated accounts are not attempted
