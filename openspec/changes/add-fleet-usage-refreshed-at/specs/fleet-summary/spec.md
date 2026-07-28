## ADDED Requirements

### Requirement: Fleet summary distinguishes usage freshness from auth refresh

Each account returned by `GET /api/fleet/summary` SHALL include nullable
`usageRefreshedAt`. When usage is visible and standard usage samples exist, the
field MUST equal the newest `recorded_at` among the usage samples already
loaded to build that account summary. Computing the field MUST NOT add a
database query.

`lastRefreshAt` SHALL remain backward compatible and SHALL continue to
represent the account's OAuth token-refresh timestamp. The existing fleet
usage-visibility policy SHALL govern `usageRefreshedAt`; the field MUST be
`null` when usage is hidden. The response MUST continue to exclude sensitive
fleet data.

#### Scenario: Summary reports the newest loaded usage sample

- **GIVEN** an account has loaded standard usage samples with different `recorded_at` values
- **WHEN** an authorized client calls `GET /api/fleet/summary`
- **THEN** the account's `usageRefreshedAt` equals the newest sample timestamp
- **AND** `lastRefreshAt` continues to equal the OAuth token-refresh timestamp

#### Scenario: Account has no usage sample

- **GIVEN** an account has no standard usage sample
- **WHEN** an authorized client calls `GET /api/fleet/summary`
- **THEN** the account's `usageRefreshedAt` is `null`

#### Scenario: Usage visibility is disabled

- **WHEN** a valid API key cannot view fleet usage
- **OR** the global API-key quota privacy setting hides upstream quota data
- **THEN** `GET /api/fleet/summary` returns `usageRefreshedAt: null`
- **AND** no hidden usage or sensitive fleet data is exposed

#### Scenario: Successful Force Probe advances usage freshness independently

- **GIVEN** an account has an existing usage sample and OAuth refresh timestamp
- **WHEN** a successful Force Probe persists a newer standard usage sample without refreshing the OAuth token
- **THEN** a later fleet summary reports a later `usageRefreshedAt`
- **AND** reports the unchanged `lastRefreshAt`

#### Scenario: Successful fleet refresh advances usage freshness independently

- **GIVEN** an account has an existing usage sample and OAuth refresh timestamp
- **WHEN** `POST /api/fleet/refresh` persists a newer standard usage sample without refreshing the OAuth token
- **THEN** a later fleet summary reports a later `usageRefreshedAt`
- **AND** reports the unchanged `lastRefreshAt`
