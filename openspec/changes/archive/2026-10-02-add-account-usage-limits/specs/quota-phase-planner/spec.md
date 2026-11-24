## ADDED Requirements

### Requirement: Quota warmup reauthorizes account usage limits before probe dispatch

Quota warmup planning MUST exclude an account whose enabled usage limit is `reached` or `data_unavailable`. After claiming a decision and acquiring any API-key reservation, execution MUST reload the account, its policy, and its standard observations in one read, MUST require the account to remain `active`, and MUST evaluate the usage limit again immediately before sending the probe. A missing account MUST skip with reason `account_not_found`, a non-active account with `account_status_<status>`, and a `reached` or `data_unavailable` policy with `account_usage_limit_reached`. A failed read MUST skip with `account_usage_limit_authorization_failed`, and a cancelled read MUST skip with `account_usage_limit_authorization_cancelled` and then propagate cancellation; neither MAY be recorded as `account_usage_limit_reached`.

Every denial MUST roll back the authorization transaction, including after a failed statement or a connection invalidated by cancellation, release any API-key reservation, move the decision from `executing` to `skipped`, and send no probe, all before cancellation propagates. SQLite diagnostic listeners MUST NOT reconnect an invalidated connection or block that rollback. Disabled and `available` policies MUST keep normal planning and execution behavior.

#### Scenario: Limit reached after quota warmup planning

- **GIVEN** a quota warmup was planned while the account policy was available
- **AND** a newer observation reaches the maximum before execution
- **WHEN** the execution gate evaluates the account again
- **THEN** the decision is skipped with reason `account_usage_limit_reached`
- **AND** no synthetic request is sent

#### Scenario: Account pauses after the claim

- **GIVEN** a claimed quota warmup holds an API-key reservation
- **WHEN** the account becomes paused before final authorization
- **THEN** the decision is skipped with reason `account_status_paused`
- **AND** the reservation is released and no synthetic request is sent

#### Scenario: Cancellation interrupts the authorization statement

- **GIVEN** a warmup has a committed execution claim and a real API-key reservation
- **WHEN** cancellation interrupts its final authorization SQL statement
- **THEN** cleanup restores a usable transaction and releases the reserved capacity
- **AND** the decision becomes `skipped` with reason `account_usage_limit_authorization_cancelled`
- **AND** cancellation propagates without a probe being sent
