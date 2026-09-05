## ADDED Requirements

### Requirement: Quota warmup reauthorizes account usage limits before probe dispatch

Quota warmup planning MUST exclude an already-evaluated account state whose enabled usage-limit state is `reached` or `data_unavailable`. After atomically claiming a planned decision and acquiring any API-key reservation, execution MUST freshly load the account and atomically read its current usage-limit policy plus standard primary, secondary, and monthly observations. It MUST require the fresh account status to remain `active` and MUST apply the canonical standard usage-limit evaluator and shape rules immediately before sending the synthetic probe.

A missing fresh account MUST skip with reason `account_not_found`; any fresh non-active account MUST skip with reason `account_status_<status>`; and a `reached` or `data_unavailable` evaluation MUST skip with reason `account_usage_limit_reached`. If the final authorization read fails, execution MUST skip with reason `account_usage_limit_authorization_failed`; if that read is cancelled, it MUST use reason `account_usage_limit_authorization_cancelled` and propagate cancellation after cleanup. Every denial MUST release any API-key reservation, transition the claimed decision from `executing` to `skipped`, and MUST NOT send the probe. Authorization failure or cancellation MUST NOT be persisted as `account_usage_limit_reached`. Disabled and `available` policies MUST preserve normal short-window planning and execution behavior.

#### Scenario: Limit reached after quota warmup planning

- **GIVEN** a quota warmup was planned while the account policy was available
- **AND** a newer standard observation reaches the enabled maximum before execution
- **WHEN** the execution gate re-evaluates the account
- **THEN** the decision is skipped with reason `account_usage_limit_reached`
- **AND** no synthetic upstream request is sent

#### Scenario: Account pauses after quota warmup planning

- **GIVEN** a quota warmup was planned while the account was active
- **AND** the account becomes paused after the decision claim or API-key reservation
- **WHEN** the final execution authorization reloads the account
- **THEN** the decision is skipped with reason `account_status_paused`
- **AND** any API-key reservation is released
- **AND** no synthetic upstream request is sent

#### Scenario: Final quota warmup authorization fails

- **GIVEN** a quota warmup decision is claimed and holds an API-key reservation
- **WHEN** its final standard-usage authorization read fails
- **THEN** the decision is skipped with reason `account_usage_limit_authorization_failed`
- **AND** the API-key reservation is released
- **AND** no synthetic upstream request is sent


### Requirement: Authorization cleanup recovers a failed database transaction

Before settling a denied or cancelled claimed warmup, execution MUST roll back the authorization read transaction, including when an actual database statement fails or the driver connection is invalidated by cancellation. It MUST finish reservation release and the `executing` to `skipped` transition before propagating cancellation. SQLite diagnostic listeners MUST NOT reconnect an invalidated connection or prevent this rollback.

#### Scenario: Cancellation interrupts an actual authorization SQL statement

- **GIVEN** a warmup has a committed execution claim and a real API-key usage reservation
- **WHEN** cancellation interrupts its final authorization SQL operation
- **THEN** cleanup restores a usable transaction and releases the reservation's reserved capacity
- **AND** the decision becomes `skipped` with reason `account_usage_limit_authorization_cancelled`
- **AND** cancellation propagates and no probe is dispatched
