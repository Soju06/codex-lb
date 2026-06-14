## ADDED Requirements

### Requirement: OAuth account creation invalidates account and dashboard caches

After an OAuth flow successfully creates or refreshes an account, the SPA SHALL invalidate cached account and dashboard queries that surface account membership or account-derived dashboard data. The invalidation SHALL include the account list, account trend queries, dashboard overview, and dashboard projections.

The invalidation helper SHALL be reusable without importing account hook modules into OAuth hook tests.

#### Scenario: Manual browser OAuth success refreshes dashboard-visible account data

- **WHEN** a browser OAuth callback is submitted manually
- **AND** the OAuth callback response reports success
- **THEN** the SPA invalidates the account list query
- **AND** invalidates account trend queries
- **AND** invalidates the dashboard overview query
- **AND** invalidates the dashboard projections query

#### Scenario: Browser OAuth status success refreshes dashboard-visible account data

- **WHEN** a browser OAuth flow starts with a tracked flow id
- **AND** the OAuth status endpoint later reports success
- **THEN** the SPA invalidates the account list query
- **AND** invalidates account trend queries
- **AND** invalidates the dashboard overview query
- **AND** invalidates the dashboard projections query

#### Scenario: Device OAuth completion refreshes dashboard-visible account data

- **WHEN** a device-code OAuth completion request succeeds
- **THEN** the SPA invalidates the account list query
- **AND** invalidates account trend queries
- **AND** invalidates the dashboard overview query
- **AND** invalidates the dashboard projections query

#### Scenario: Failed OAuth does not refresh dashboard-visible account data

- **WHEN** an OAuth completion or callback request fails
- **THEN** the SPA does not invalidate account or dashboard queries for that failed OAuth attempt
