## MODIFIED Requirements

### Requirement: Usage capacity recognizes upstream ChatGPT plan types

The system MUST recognize upstream account plans for capacity. The Business Pro
Lite alias MUST canonicalize to `prolite` at account, usage, model, and rate-limit
boundaries. Pro Lite MUST use `1125.0` primary and `37800.0` secondary capacity.
`promax` MUST retain its stored identifier and use Pro estimates of `1500.0`
primary and `50400.0` secondary credits. Existing identity, status, quota, and
operator-policy gates MUST remain in force.

#### Scenario: Pro Lite account contributes aggregate remaining credits

- **GIVEN** an active account whose stored `plan_type` is `prolite`
- **AND** its latest primary and secondary usage rows report `used_percent` below 100
- **WHEN** the system builds usage window summaries or per-account remaining credit values
- **THEN** the account contributes `1125.0` primary capacity and `37800.0` secondary capacity
- **AND** the computed remaining credits are non-zero according to the reported usage percent

#### Scenario: Business Pro Lite refresh uses the existing Pro Lite tier

- **GIVEN** a workspace-less account with a recognized paid plan
- **WHEN** usage refresh returns a valid payload with `plan_type` `self_serve_business_prolite`
- **THEN** the usage sample is written and the account plan becomes `prolite`
- **AND** metadata, capacity, model eligibility, and rate-limit projection use `prolite`

#### Scenario: Pro Max capacity preserves its plan identifier

- **GIVEN** an active account whose stored `plan_type` is `promax`
- **WHEN** the dashboard builds usage summaries and remaining credits
- **THEN** its primary and secondary capacity estimates are `1500.0` and `50400.0`
- **AND** its displayed plan remains `promax`

#### Scenario: Pro Max capacity-aware routing avoids the unknown-plan fallback

- **GIVEN** active `promax` and `pro` accounts with equal usage and reset deadlines
- **AND** the `promax` account has no explicit capacity override
- **WHEN** capacity-weighted or relative-availability routing selects an account
- **THEN** both accounts use the same Pro capacity estimate

## ADDED Requirements

### Requirement: Pro Max accounts are eligible for Pro-gated models

The system MUST treat recognized `promax` accounts as Pro-equivalent when evaluating model registry plan eligibility, without changing their stored plan identifier. Existing account status, workspace identity, quota, and operator-policy gates MUST continue to apply.

#### Scenario: Pro Max account passes the Pro model gate

- **GIVEN** an active `promax` account with usable quota
- **AND** a requested model allowed for `pro` accounts
- **WHEN** proxy account selection evaluates the account
- **THEN** the account remains eligible for selection
- **AND** its stored plan remains `promax`
