## ADDED Requirements

### Requirement: Pro Max accounts use Pro-equivalent capacity and model eligibility

The system MUST recognize the upstream account plan type `promax` as a
supported account plan. Until measured Pro Max capacity values exist, `promax`
MUST use the Pro capacity values (`1500.0` primary and `50400.0` secondary)
when calculating absolute usage capacity and capacity-aware routing weights,
instead of the unknown-plan fallback. The system MUST treat `promax` as
Pro-equivalent when evaluating model registry plan eligibility, and MUST
preserve the stored `promax` value for display and request-log context.

#### Scenario: Pro Max account contributes Pro capacity

- **GIVEN** an active account whose stored `plan_type` is `promax`
- **AND** its latest primary and secondary usage rows report `used_percent` below 100
- **WHEN** the system builds usage window summaries or per-account remaining credit values
- **THEN** the account contributes `1500.0` primary capacity and `50400.0` secondary capacity

#### Scenario: Pro Max account is weighted like a Pro account

- **GIVEN** an active `promax` account without stored capacity credits
- **AND** an active `pro` account with the same usage
- **WHEN** capacity-weighted or relative-availability routing selects an account
- **THEN** the `promax` account is weighted with Pro capacity, not the unknown-plan fallback

#### Scenario: Pro Max account can be selected for a Pro-gated model

- **GIVEN** an active account whose stored `plan_type` is `promax`
- **AND** the requested model is allowed for `pro` accounts by the model registry
- **WHEN** proxy account selection evaluates eligible accounts for the requested model
- **THEN** the Pro Max account remains eligible for selection
- **AND** the selection does not fail with `no_accounts`
