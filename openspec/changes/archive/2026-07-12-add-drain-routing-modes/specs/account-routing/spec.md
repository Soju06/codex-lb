## ADDED Requirements

### Requirement: Sequential drain routing
The proxy account selector SHALL support a `sequential_drain` routing strategy. The strategy SHALL evaluate only accounts that pass the existing eligibility, model-plan, quota, cooldown, circuit-breaker, and budget-safety gates, then select the usable account with the lowest effective secondary capacity before moving to higher-capacity accounts.

#### Scenario: Lowest-capacity usable account is drained first
- **GIVEN** multiple healthy eligible accounts with different effective secondary capacities
- **WHEN** account selection uses `sequential_drain`
- **THEN** the account with the lowest effective secondary capacity is selected

#### Scenario: Exhausted lower-capacity accounts are skipped
- **GIVEN** the lowest-capacity account has no usable quota
- **WHEN** account selection uses `sequential_drain`
- **THEN** the selector chooses the next-lowest usable capacity account

### Requirement: Reset drain routing
The proxy account selector SHALL support a `reset_drain` routing strategy. The strategy SHALL evaluate only accounts that pass the existing eligibility, model-plan, quota, cooldown, circuit-breaker, and budget-safety gates, then prefer usable accounts whose secondary quota reset is nearest. When secondary reset data is unavailable, it SHALL fall back to the primary reset time. Within the same reset bucket, it SHALL prefer the account with more remaining usable quota.

#### Scenario: Soonest resetting usable account is selected
- **GIVEN** multiple healthy eligible accounts with usable quota
- **AND** their secondary quota windows reset at different times
- **WHEN** account selection uses `reset_drain`
- **THEN** the usable account with the nearest secondary reset is selected

#### Scenario: Same-reset accounts drain higher remaining quota first
- **GIVEN** multiple healthy eligible accounts in the same reset bucket
- **WHEN** account selection uses `reset_drain`
- **THEN** the account with more remaining usable quota is selected

### Requirement: Single-account routing
The proxy routing layer SHALL support a `single_account` routing strategy configured by `single_account_id`. When enabled, the proxy SHALL route only through the configured account if that account exists, is available, and matches the requested model-plan scope. If the setting is missing, unavailable, or incompatible with the request, the proxy SHALL fail the request with a routing error instead of silently falling back to another account.

#### Scenario: Configured account serves matching traffic
- **GIVEN** `single_account` routing is enabled with a configured available account
- **AND** the account matches the requested model-plan scope
- **WHEN** the proxy selects an account
- **THEN** the configured account is selected

#### Scenario: Missing or unavailable selected account does not fall back
- **GIVEN** `single_account` routing is enabled
- **AND** the configured account is missing, unavailable, exhausted, or outside the requested model-plan scope
- **WHEN** the proxy selects an account
- **THEN** no alternate account is selected
- **AND** the request fails with a routing error

### Requirement: Drain routing dashboard settings
Dashboard settings SHALL expose `sequential_drain`, `reset_drain`, and `single_account` as valid routing strategies. When `single_account` is selected, the dashboard SHALL allow choosing the configured account id and the backend SHALL persist it as nullable `single_account_id`.

#### Scenario: Operator saves a single-account route
- **WHEN** an operator selects `single_account` and chooses an account
- **THEN** the settings API persists the selected account id
- **AND** subsequent settings responses include that id
