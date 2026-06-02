## ADDED Requirements

### Requirement: Account summary duplicate email indicator

The dashboard accounts API SHALL expose an `isEmailDuplicate` boolean on each
`AccountSummary` returned by `GET /api/accounts`. The field MUST be `true` when
another account row in the same response has the same real email address and
the same ChatGPT account identity, and MUST be `false` for unique real
email/identity pairs. Missing, blank, and legacy placeholder emails equal to
`DEFAULT_EMAIL` (`unknown@example.com`) MUST be excluded from duplicate
detection and MUST NOT be flagged as duplicates. Rows that share an email but
belong to different ChatGPT account identities MUST NOT be flagged as
duplicates.

#### Scenario: Duplicate real email and identity pairs are flagged

- **WHEN** `GET /api/accounts` returns two or more account rows with the same real non-placeholder email and the same ChatGPT account identity
- **THEN** every row in that email and identity group includes `isEmailDuplicate: true`

#### Scenario: Same email across identities is not flagged

- **WHEN** `GET /api/accounts` returns account rows with the same real non-placeholder email but different ChatGPT account identities
- **THEN** those rows include `isEmailDuplicate: false`

#### Scenario: Placeholder emails are ignored

- **WHEN** `GET /api/accounts` returns two or more account rows whose email is `unknown@example.com`
- **THEN** those rows include `isEmailDuplicate: false`

#### Scenario: Unique emails are not flagged

- **WHEN** `GET /api/accounts` returns an account row with an email that appears only once in the response
- **THEN** that row includes `isEmailDuplicate: false`
