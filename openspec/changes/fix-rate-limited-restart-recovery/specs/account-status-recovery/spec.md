## ADDED Requirements

### Requirement: Rate-limited accounts recover automatically once the quota window resets

The balancer SHALL clear an account's `RATE_LIMITED` status to `ACTIVE` when a usage snapshot recorded **after** the account's persisted `blocked_at` reports the decision window below 100% used, even if the in-memory runtime cooldown was lost (for example after a process restart). The persisted `reset_at` MUST NOT keep the account blocked once that evidence exists.

#### Scenario: Recovery after restart with fresh usage below the limit

- **WHEN** an account is persisted as `RATE_LIMITED` with a `reset_at` in the future and a `blocked_at` older than the rate-limit debounce (120 s)
- **AND** a usage snapshot recorded after `blocked_at` reports the primary window below 100% used
- **THEN** the reconstructed account state is `ACTIVE`
- **AND** the state's `reset_at` is cleared

#### Scenario: No recovery while the runtime cooldown is still active

- **WHEN** an account is `RATE_LIMITED` and its in-memory cooldown has not yet expired
- **THEN** the account remains `RATE_LIMITED` regardless of usage snapshots

#### Scenario: No recovery when the window is still exhausted

- **WHEN** an account is `RATE_LIMITED` and the newest usage snapshot reports the primary window at 100% used
- **THEN** the reconstructed state MUST NOT be `ACTIVE`
- **AND** it is reported as `QUOTA_EXCEEDED` carrying the reset marker so selection stays blocked until the reset

#### Scenario: Recovery does not require a restart

- **WHEN** an account is `RATE_LIMITED`, the in-memory cooldown has expired, and a usage snapshot recorded after `blocked_at` reports the window below 100% used
- **THEN** the account becomes `ACTIVE` without operator intervention

### Requirement: Periodic usage refresh reconciles blocked account statuses

The background usage refresh SHALL reconcile persisted account statuses so that accounts whose quota window has reset stop being reported as blocked without waiting for proxy traffic or a manual `Reactivate`.

#### Scenario: Idle blocked account clears on the refresh cycle

- **WHEN** the usage refresh scheduler completes a cycle and an account is persisted as `RATE_LIMITED` or `QUOTA_EXCEEDED`
- **AND** the freshly read usage snapshots show its window below 100% used and the block debounce has elapsed
- **THEN** the scheduler persists the account status as `ACTIVE` with `reset_at` and `blocked_at` cleared
- **AND** logs the number of recovered accounts

#### Scenario: Reconciliation never overrides a concurrent update

- **WHEN** the persisted account status, `reset_at`, or `blocked_at` changed after the reconciliation read the account
- **THEN** the reconciliation write MUST be skipped (optimistic `update_status_if_current`) and the account left untouched

#### Scenario: Healthy accounts are untouched

- **WHEN** an account status is neither `RATE_LIMITED` nor `QUOTA_EXCEEDED`
- **THEN** the reconciliation MUST NOT write any status update for it
