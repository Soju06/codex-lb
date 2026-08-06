# account-routing

## ADDED Requirements

### Requirement: Accounts have a reversible maximum-usage policy

Each account SHALL support an optional maximum standard-quota used percentage greater than 0 and at most 100, plus an enabled state. The policy SHALL default to disabled for existing and new accounts. Disabling a configured policy SHALL retain its percentage for later re-enablement, while removing the policy SHALL clear the percentage and disable it. The API MUST reject an enabled policy without a percentage and MUST reject percentages outside the supported range.

#### Scenario: Operator temporarily disables a configured limit

- **GIVEN** an account has an enabled maximum usage of 10 percent
- **WHEN** the operator disables the policy without removing it
- **THEN** the account retains 10 percent as its configured value
- **AND** routing does not apply that policy until it is re-enabled

#### Scenario: Operator removes a configured limit

- **GIVEN** an account has a configured maximum usage
- **WHEN** the operator removes the policy
- **THEN** the percentage is cleared
- **AND** the policy is disabled

### Requirement: Account usage limits are hard routing eligibility gates

For an account with an enabled maximum usage policy, the selector MUST evaluate current standard primary and long-window quota observations after normalizing weekly-only and monthly-only account shapes. If any current standard window reports used percentage greater than or equal to the configured maximum, the account MUST be excluded before sticky affinity, single-account routing, manual routing policy, additional-quota routing, health-tier selection, backoff fallback, or any routing strategy is applied. Standard usage limits MUST NOT be bypassed by an additional-quota request that ignores standard upstream exhaustion. Reaching a local account policy MUST NOT mutate the account's persisted upstream status.

#### Scenario: Equality reaches the limit

- **GIVEN** account A has an enabled maximum usage of 10 percent
- **AND** its current weekly usage observation is exactly 10 percent used
- **WHEN** any proxy route selects an account
- **THEN** account A is not eligible

#### Scenario: Another account is selected below the limit

- **GIVEN** account A has reached its enabled maximum usage
- **AND** account B is otherwise eligible and below its enabled maximum usage
- **WHEN** the proxy selects an account
- **THEN** it selects account B
- **AND** no fallback selects account A

#### Scenario: Additional quota does not bypass the standard account policy

- **GIVEN** account A has available additional quota for the requested model
- **AND** account A has reached its enabled standard maximum usage
- **WHEN** gated-model routing ignores standard upstream quota status
- **THEN** account A remains excluded by the operator's maximum-usage policy

#### Scenario: Reset usage makes the account eligible again

- **GIVEN** an account was excluded because a standard window reached its maximum usage
- **WHEN** a current post-reset usage observation reports every available standard window below the maximum
- **THEN** the account becomes eligible without changing or removing the policy

### Requirement: Enabled account usage limits fail closed without current data

An enabled maximum-usage policy MUST require current standard quota data. Elapsed window rows MUST NOT count as exhaustion evidence, but if no current relevant standard observation remains, or a relevant observation is stale or lacks a used percentage, the account MUST be excluded with policy state `data_unavailable`. When all otherwise eligible candidates are excluded by `reached` or `data_unavailable` usage-limit state, selection MUST return stable error code `account_usage_limit_reached` and MUST NOT report the accounts as upstream rate-limited.

#### Scenario: Missing observations preserve the account quota

- **GIVEN** an account has an enabled maximum usage policy
- **AND** no current standard usage observation is available
- **WHEN** the selector evaluates the account
- **THEN** the account is not selected
- **AND** its usage-limit state is `data_unavailable`

#### Scenario: All accounts are locally capped

- **GIVEN** every otherwise eligible account has limit state `reached` or `data_unavailable`
- **WHEN** the proxy attempts selection
- **THEN** no account is selected
- **AND** the routing error code is `account_usage_limit_reached`

### Requirement: Dashboard account controls expose usage-limit state and precision

Account summaries SHALL expose the configured percentage, enabled flag, and evaluated state (`disabled`, `available`, `reached`, or `data_unavailable`). The Accounts dashboard SHALL allow an operator to set, edit, enable, disable, and remove the policy. It SHALL describe a value of 10 percent as a maximum of 10 percent used (90 percent reserved) and SHALL warn that delayed upstream observations or already in-flight requests can move actual usage past the displayed percentage before the gate observes it.

#### Scenario: Enabled limit is visible and toggleable

- **GIVEN** an account has a configured 10 percent maximum that is disabled
- **WHEN** the operator views the account
- **THEN** the dashboard shows 10 percent maximum used and 90 percent reserved
- **AND** the operator can enable it without re-entering the value

#### Scenario: Reached policy is distinguishable from upstream exhaustion

- **GIVEN** an active account has reached its enabled maximum usage
- **WHEN** the account is shown in the dashboard
- **THEN** the dashboard identifies the local usage limit as reached
- **AND** it does not relabel the persisted account status as upstream quota exhausted
