## ADDED Requirements

### Requirement: Manual account routing policy

Each account SHALL have a persisted manual routing policy with one of `normal`, `burn_first`, or `preserve`. Missing or legacy values SHALL be treated as `normal`.

#### Scenario: expendable accounts are selected before normal accounts

- **GIVEN** at least one eligible account has routing policy `burn_first`
- **AND** at least one eligible account has routing policy `normal`
- **WHEN** the load balancer selects an account
- **THEN** it selects from the `burn_first` pool before considering `normal` accounts

#### Scenario: preserved accounts are fallback only

- **GIVEN** at least one eligible account has routing policy `normal`
- **AND** at least one eligible account has routing policy `preserve`
- **WHEN** the load balancer selects an account
- **THEN** it selects from the `normal` pool before considering `preserve` accounts

#### Scenario: routing policy does not bypass eligibility gates

- **GIVEN** a request is filtered by model plan or additional quota eligibility
- **WHEN** an account has routing policy `burn_first`
- **THEN** that account is still excluded if it fails the model plan or additional quota gate
