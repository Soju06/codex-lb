## MODIFIED Requirements

### Requirement: Credit-backed secondary quota remains usable

When account status is derived from persisted usage snapshots, an exhausted secondary-window usage percentage MUST NOT by itself reactivate a persisted `quota_exceeded` account unless the governing usage snapshot reports usable credit-backed capacity. Usable credit-backed capacity is present only when `credits_unlimited` is true or `credits_balance` is positive. A bare `credits_has` value of true with `credits_balance` missing or zero MUST NOT override exhausted secondary-window usage.

This credit-aware interpretation MUST be shared by proxy account selection and account/dashboard summary status mapping so an account selected as usable by the proxy is not simultaneously displayed as `quota_exceeded` in the operator summary. This requirement does not make local long-window usage snapshots authoritative for foreground proxy selection when account-routing treats them as advisory.

#### Scenario: Secondary quota exhausted with credits remains active

- **GIVEN** an account is persisted as `quota_exceeded`
- **AND** its governing secondary-window usage reports `used_percent >= 100`
- **AND** the same usage snapshot reports usable credit-backed capacity
- **WHEN** proxy selection or account-summary mapping derives the effective status
- **THEN** the effective status is `active`

#### Scenario: Secondary quota exhausted with only a bare has-credits flag stays quota-exceeded

- **GIVEN** an account is persisted as `quota_exceeded`
- **AND** its governing secondary-window usage reports `used_percent >= 100`
- **AND** the newest usage sample with credit metadata reports `credits_has = true`
- **AND** `credits_unlimited` is false
- **AND** `credits_balance` is missing or zero
- **WHEN** proxy selection or account-summary mapping derives the effective status
- **THEN** the effective status remains `quota_exceeded`
- **AND** the reset guard points at the secondary reset time

### Requirement: Credit-backed usage remains selectable after quota windows fill

When deriving effective account status from upstream usage samples, the system MUST treat the latest spendable credit metadata as an override for secondary quota-derived blocking state. If the latest usage sample with credit metadata reports `credits_unlimited = true` or `credits_balance > 0`, then secondary quota windows at `100%` MUST NOT by themselves keep the account `quota_exceeded`. If the latest usage sample reports only `credits_has = true` with `credits_unlimited = false` and `credits_balance` missing or zero, that sample MUST NOT override secondary quota exhaustion. Primary-window exhaustion MUST keep `rate_limited` precedence when status inference from usage is enabled.

This override MUST NOT reactivate accounts that are explicitly `paused` or
`deactivated`. When multiple usage samples carry credit metadata, the newest
sample by `recorded_at` MUST be used.

#### Scenario: Credit-backed weekly account remains selectable

- **GIVEN** an account is otherwise routable
- **AND** its weekly usage window reports `used_percent = 100`
- **AND** its primary usage window is below `100`
- **AND** the newest usage sample with credit metadata reports a positive credit balance
- **WHEN** the load balancer derives account state
- **THEN** the derived status remains `active`
- **AND** the account remains eligible for selection

#### Scenario: Persisted weekly quota block with only a bare has-credits flag is not selectable

- **GIVEN** an account is persisted as `quota_exceeded`
- **AND** its weekly usage window reports `used_percent = 100`
- **AND** its primary usage window is below `100`
- **AND** the newest usage sample with credit metadata reports `credits_has = true`
- **AND** `credits_unlimited` is false
- **AND** `credits_balance` is missing or zero
- **WHEN** the load balancer derives account state
- **THEN** the derived status remains `quota_exceeded`
- **AND** the account is not eligible for selection until that quota window resets or a later snapshot reports spendable credits

#### Scenario: Credit-backed account remains rate-limited when primary window is exhausted

- **GIVEN** an account is otherwise routable
- **AND** its primary usage window reports `used_percent = 100`
- **AND** the newest usage sample with credit metadata reports a positive credit balance
- **WHEN** the load balancer derives account state
- **THEN** the derived status is `rate_limited`
- **AND** the reset guard points at the primary reset time

#### Scenario: Newer zero-credit sample removes the override

- **GIVEN** an older usage sample reports available credits
- **AND** a newer usage sample reports no unlimited credits and zero credit balance
- **WHEN** quota status is derived from usage
- **THEN** the newer zero-credit sample is authoritative
- **AND** a persisted full long-window quota block is not cleared by a bare `credits_has` flag

#### Scenario: Paused account is not reactivated by credits

- **GIVEN** an account is paused
- **AND** its newest usage sample reports available credits
- **WHEN** quota status is derived from usage
- **THEN** the account remains paused
