## MODIFIED Requirements

### Requirement: Fleet summary requires API key authentication

The system SHALL expose `GET /api/fleet/summary` for trusted local fleet
consumers. The route MUST require a valid Bearer API key even when global proxy
API-key authentication is disabled. For callers allowed to view upstream
usage, each account SHALL expose `lastRefreshAt` as OAuth token freshness and
`usageRefreshedAt` as quota-snapshot freshness. `usageRefreshedAt` MUST equal
the newest `recorded_at` value among the persisted usage samples used to build
that account summary, or `null` when no such sample exists.

#### Scenario: Missing fleet summary key is rejected

- **WHEN** a client calls `GET /api/fleet/summary` without a Bearer token
- **THEN** the system returns 401
- **AND** no account summary payload is returned

#### Scenario: Valid fleet summary key returns account capacity

- **WHEN** a client calls `GET /api/fleet/summary` with a valid Bearer API key
- **THEN** the response includes `accounts[]`
- **AND** each account includes `accountId`, `displayName`, `email`, `status`,
  `planType`, `primary`, `secondary`, `lastRefreshAt`, and `usageRefreshedAt`
- **AND** each window includes `remainingPercent`, `resetAt`, and `windowMinutes`

#### Scenario: Usage refresh advances independently of OAuth refresh

- **GIVEN** an account has an existing quota snapshot and OAuth refresh time
- **WHEN** force probe or fleet refresh persists a newer usage sample without
  refreshing OAuth credentials
- **THEN** `usageRefreshedAt` advances to the newer usage sample time
- **AND** `lastRefreshAt` remains unchanged

#### Scenario: Usage freshness is unavailable

- **WHEN** an account has no persisted usage sample
- **THEN** `usageRefreshedAt` is `null`

#### Scenario: Usage visibility is denied

- **WHEN** the authenticated key cannot view upstream usage
- **THEN** `usageRefreshedAt` is `null`
- **AND** `lastRefreshAt` is `null`
