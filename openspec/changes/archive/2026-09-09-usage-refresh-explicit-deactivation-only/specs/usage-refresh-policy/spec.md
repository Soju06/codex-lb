## MODIFIED Requirements

### Requirement: Usage refresh deactivates on clear deactivation signals

The system MUST change an account to `deactivated` or `reauth_required` during
usage refresh only when the upstream error contains an explicit,
account-specific terminal signal. A recognized permanent-failure error code
MUST be mapped through the existing permanent-failure account-status policy,
and an error message that explicitly says the account is deactivated MUST mark
the account `deactivated`. A bare HTTP status, including `402` or `404`, MUST
NOT change account status or routing availability and MUST remain a refresh
failure for existing logging, error accounting, and later refresh retries.

#### Scenario: Bare usage 404 preserves account status

- **GIVEN** an account eligible for background usage refresh
- **WHEN** the usage endpoint returns HTTP `404` without an explicit permanent-failure code or deactivation message
- **THEN** the account's current status is unchanged
- **AND** the account is not marked routing-unavailable
- **AND** the response is recorded as a refresh failure and a later refresh cycle may retry it

#### Scenario: Bare usage 402 preserves account status

- **GIVEN** an account eligible for background usage refresh
- **WHEN** the usage endpoint returns HTTP `402` without an explicit permanent-failure code or deactivation message
- **THEN** the account's current status is unchanged
- **AND** the account is not marked routing-unavailable
- **AND** the response is recorded as a refresh failure and a later refresh cycle may retry it

#### Scenario: Usage 401 app session terminated requires re-authentication

- **WHEN** usage refresh receives HTTP `401`
- **AND** the upstream error code is `app_session_terminated`
- **THEN** the account is marked `reauth_required`
- **AND** later usage refresh cycles skip that account until re-authentication

#### Scenario: Explicit usage deactivation message deactivates account

- **WHEN** usage refresh receives an error whose message says `your OpenAI account has been deactivated`
- **AND** the error has no recognized permanent-failure code
- **THEN** the account is marked `deactivated`
- **AND** the account is marked routing-unavailable
