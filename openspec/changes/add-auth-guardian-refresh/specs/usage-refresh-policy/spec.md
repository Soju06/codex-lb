## ADDED Requirements

### Requirement: Proactive active account credential refresh

Codex-LB SHALL periodically refresh active account credentials in the background when an active account's last refresh is older than a configured maximum age.

#### Scenario: Idle active account becomes stale

- **GIVEN** an account has status `active`
- **AND** its `last_refresh` is older than the configured Auth Guardian max age
- **WHEN** Auth Guardian runs on the elected leader
- **THEN** Codex-LB force-refreshes that account without requiring request traffic to select it first

### Requirement: Auth Guardian bounded and safe execution

Auth Guardian SHALL bound each run by configured batch size and concurrency, add jitter/backoff, and avoid logging token material.

#### Scenario: Refresh fails for one account

- **GIVEN** Auth Guardian attempts to refresh an active account
- **WHEN** refresh fails
- **THEN** Auth Guardian records per-account backoff
- **AND** later accounts in the batch are still eligible to run
- **AND** logs do not contain token material

### Requirement: Multi-replica leader guard

Auth Guardian SHALL use the existing leader-election mechanism so only the elected replica performs proactive refresh work.

#### Scenario: Replica is not leader

- **GIVEN** leader election is enabled
- **AND** the current replica does not acquire leadership
- **WHEN** Auth Guardian wakes
- **THEN** the scheduler skips refresh work for that pass
