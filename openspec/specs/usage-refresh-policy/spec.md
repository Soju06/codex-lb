# usage-refresh-policy Specification

## Purpose
Define how background usage refresh reacts to auth-like failures without permanently hammering bad accounts.
## Requirements
### Requirement: Usage refresh cools down repeated auth-like failures

Background usage refresh MUST apply a cooldown to accounts that repeatedly fail usage refresh with ambiguous `401` or `403` responses. Accounts in that cooldown window MUST be skipped until the cooldown expires or a later successful refresh clears it.

#### Scenario: Ambiguous usage 401 enters cooldown
- **WHEN** usage refresh receives a `401` that does not match a permanent deactivation signal
- **THEN** the account is not deactivated immediately
- **AND** subsequent refresh cycles skip the account until the cooldown window expires

#### Scenario: Successful refresh clears cooldown
- **WHEN** a later usage refresh succeeds for an account that had been cooled down
- **THEN** the cooldown is cleared
- **AND** normal refresh cadence resumes

### Requirement: Usage refresh deactivates on clear deactivation signals

The system MUST deactivate accounts when usage refresh receives a permanent deactivation signal. At minimum, `402`, `404`, and `401` responses whose message explicitly indicates that the OpenAI account has been deactivated MUST be treated as deactivation signals.

#### Scenario: Usage 401 deactivation message deactivates the account
- **WHEN** usage refresh receives HTTP `401`
- **AND** the upstream message states that the OpenAI account has been deactivated
- **THEN** the account is marked `deactivated`
- **AND** later usage refresh cycles skip that account

### Requirement: token_expired at the refresh boundary deactivates the account

When the OAuth refresh endpoint fails with error code `token_expired`, the system MUST treat it as a permanent authentication failure on par with `refresh_token_expired` / `refresh_token_reused` / `refresh_token_invalidated`. The affected account MUST be deactivated and removed from the routing pool until it is re-authenticated.

#### Scenario: Refresh-time `token_expired` is classified as permanent

- **WHEN** `classify_refresh_error("token_expired")` is evaluated
- **THEN** it returns `True`

#### Scenario: Refresh-time `token_expired` deactivates the account

- **WHEN** `AuthManager.refresh_account` receives a `RefreshError("token_expired", ..., is_permanent=True)` from `refresh_access_token`
- **THEN** the account is transitioned to `DEACTIVATED`
- **AND** the deactivation reason references the re-login requirement so the dashboard can surface it
- **AND** the account is no longer selected by the load balancer until it is re-authenticated

#### Scenario: Usage-refresh-time `token_expired` deactivates the account

- **WHEN** background usage refresh observes an upstream error whose code is `token_expired` (via `_should_deactivate_for_usage_error`'s permanent-code check)
- **THEN** the account is transitioned to `DEACTIVATED` immediately, without entering the ambiguous-401 cooldown loop

### Requirement: Usage capacity recognizes upstream ChatGPT plan types

The system MUST recognize account plan types returned by upstream ChatGPT auth and usage payloads when calculating absolute usage capacity. `prolite` MUST be treated as a supported account plan with Plus x5 capacity values (`1125.0` primary and `37800.0` secondary), while preserving the stored plan type value for display and request-log context.

#### Scenario: Pro Lite account contributes aggregate remaining credits

- **GIVEN** an active account whose stored `plan_type` is `prolite`
- **AND** its latest primary and secondary usage rows report `used_percent` below 100
- **WHEN** the system builds usage window summaries or per-account remaining credit values
- **THEN** the account contributes `1125.0` primary capacity and `37800.0` secondary capacity
- **AND** the computed remaining credits are non-zero according to the reported usage percent

### Requirement: Pro Lite accounts are eligible for Pro-gated models

The system MUST treat stored `prolite` account plan types as Pro-equivalent when evaluating model registry plan eligibility, while preserving the stored `prolite` value for display and request-log context.

#### Scenario: Pro Lite account can be selected for a Pro-gated model

- **GIVEN** an active account whose stored `plan_type` is `prolite`
- **AND** its latest primary and secondary usage rows are below the configured usage threshold
- **AND** the requested model is allowed for `pro` accounts by the model registry
- **WHEN** proxy account selection evaluates eligible accounts for the requested model
- **THEN** the Pro Lite account remains eligible for selection
- **AND** the selection does not fail with `no_accounts`

### Requirement: Background usage refresh reconciles recoverable blocked statuses
Background usage refresh SHALL reconcile persisted `rate_limited` and `quota_exceeded` accounts back to `active` after it writes fresh usage snapshots that prove the blocked window has recovered. This reconciliation SHALL be recovery-only and SHALL NOT promote `active` accounts into blocked statuses.

#### Scenario: Scheduler recovers a stale rate-limited account from fresh primary usage
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** a later background usage refresh writes a fresh primary usage row recorded after the persisted block marker
- **AND** that primary usage row reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler recovers a legacy rate-limited account without a block marker
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** the account has no persisted block marker
- **AND** a later background usage refresh writes a recent primary usage row that reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at`

#### Scenario: Scheduler preserves legacy rate-limited accounts without recent primary usage
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** the account has no persisted block marker
- **AND** the latest primary usage row is not recent enough to prove background refresh recovery
- **THEN** the scheduler leaves the account `rate_limited`

#### Scenario: Scheduler preserves an unexpired rate-limit cooldown
- **WHEN** an account is persisted as `rate_limited`
- **AND** its persisted rate-limit reset deadline is still in the future
- **AND** a later background usage refresh writes a fresh primary usage row recorded after the persisted block marker
- **AND** that primary usage row reports usage below `100%`
- **THEN** the scheduler leaves the account `rate_limited`

#### Scenario: Scheduler recovers a stale quota-exceeded account from fresh secondary usage
- **WHEN** an account is persisted as `quota_exceeded`
- **AND** a later background usage refresh writes a fresh secondary usage row that reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler does not tighten active accounts into blocked statuses
- **WHEN** background usage refresh evaluates an account currently persisted as `active`
- **THEN** the scheduler does not change that account to `rate_limited` or `quota_exceeded`

#### Scenario: Scheduler ignores stale pre-block recovery evidence
- **WHEN** an account is persisted as `rate_limited`
- **AND** the latest primary usage row was recorded before the persisted block marker
- **THEN** the scheduler leaves the account blocked

#### Scenario: Scheduler skips recovery when the account row changed concurrently
- **WHEN** background usage refresh determines that a blocked account is recoverable
- **AND** the persisted account status or reset markers change before the scheduler writes recovery
- **THEN** the scheduler skips the stale recovery write

#### Scenario: Scheduler clears stale deactivation reasons on recovery
- **WHEN** background usage refresh recovers a `rate_limited` or `quota_exceeded` account to `active`
- **THEN** the scheduler writes `deactivation_reason` as `NULL`

### Requirement: Usage refresh does not trust elapsed reset windows

Background usage refresh MUST treat a latest usage row as stale when that row's `reset_at` timestamp is in the past, even when the row's `recorded_at` timestamp is still within the normal refresh interval.

#### Scenario: Past reset_at bypasses freshness

- **GIVEN** the latest usage row was recorded within the normal refresh interval
- **AND** that row's `reset_at` timestamp has already elapsed
- **WHEN** background usage refresh evaluates the account
- **THEN** the row is treated as stale
- **AND** codex-lb attempts a fresh upstream usage fetch

### Requirement: Blocked accounts refresh once their reset deadline elapses

When an account is `RATE_LIMITED` or `QUOTA_EXCEEDED` and its persisted `reset_at` timestamp has elapsed, background usage refresh MUST bypass the normal freshness interval so the account can recover from the upstream post-reset state. The bypass MUST NOT apply before the persisted reset deadline elapses.

#### Scenario: Quota-exceeded account with fresh primary row reaches reset deadline

- **GIVEN** an account is marked `QUOTA_EXCEEDED`
- **AND** the account's persisted `reset_at` timestamp has elapsed
- **AND** the latest primary usage row is still within the normal refresh interval
- **WHEN** background usage refresh evaluates the account
- **THEN** codex-lb performs an upstream usage fetch instead of waiting for the primary row to age out

#### Scenario: Rate-limited account reaches reset deadline

- **GIVEN** an account is marked `RATE_LIMITED`
- **AND** the account's persisted `reset_at` timestamp has elapsed
- **WHEN** background usage refresh evaluates the account
- **THEN** codex-lb performs an upstream usage fetch instead of waiting for the normal refresh interval

### Requirement: Credit-backed secondary quota remains usable

When account status is derived from persisted usage snapshots, an exhausted secondary-window usage percentage MUST NOT by itself mark an account `quota_exceeded` if the governing usage snapshot reports usable credit-backed capacity. Usable credit-backed capacity is present when `credits_unlimited` is true, `credits_has` is true, or `credits_balance` is positive.

This credit-aware interpretation MUST be shared by proxy account selection and account/dashboard summary status mapping so an account selected as usable by the proxy is not simultaneously displayed as `quota_exceeded` in the operator summary. Exhausted primary-window usage MUST still take precedence as `rate_limited`, and paused or deactivated accounts MUST NOT be reactivated solely because a usage snapshot reports usable credits.

#### Scenario: Secondary quota exhausted with credits remains active

- **GIVEN** an account is persisted as `quota_exceeded`
- **AND** its governing secondary-window usage reports `used_percent >= 100`
- **AND** the same usage snapshot reports usable credit-backed capacity
- **WHEN** proxy selection or account-summary mapping derives the effective status
- **THEN** the effective status is `active`

#### Scenario: Exhausted primary window keeps rate-limit precedence

- **GIVEN** an account has usable credit-backed capacity in its usage snapshot
- **AND** its primary-window usage reports `used_percent >= 100`
- **WHEN** proxy selection or account-summary mapping derives the effective status
- **THEN** the effective status is `rate_limited`

#### Scenario: Operator-disabled states are preserved

- **GIVEN** an account is `paused` or `deactivated`
- **AND** its usage snapshot reports usable credit-backed capacity
- **WHEN** proxy selection or account-summary mapping derives the effective status
- **THEN** the account remains `paused` or `deactivated`

### Requirement: Reset-confirmed limit warm-up

The system SHALL support an optional limit warm-up mechanism that is disabled by default. When enabled globally and for an account, background usage refresh MAY send one minimal upstream Responses request after it confirms that a selected quota window has moved from an exhausted sample to a newly available reset window.

#### Scenario: Warm-up is skipped unless reset is confirmed
- **GIVEN** limit warm-up is enabled globally and for an account
- **AND** the account's previous usage sample for a selected window was exhausted
- **WHEN** background usage refresh records a newer sample for that window with `used_percent < 100` and a later `reset_at`
- **THEN** the system sends at most one warm-up request for that account/window/reset tuple

#### Scenario: Warm-up is opt-in and safe by default
- **GIVEN** background usage refresh is preparing to evaluate limit warm-up candidates
- **WHEN** global limit warm-up is disabled
- **OR** the account is not opted in
- **THEN** background usage refresh MUST NOT send warm-up traffic

#### Scenario: Warm-up uses fresh opt-in state after usage refresh
- **GIVEN** an account was loaded before a background usage refresh cycle
- **AND** the account's limit warm-up opt-in changes while the refresh cycle is running
- **WHEN** the scheduler evaluates warm-up candidates after writing usage samples
- **THEN** the scheduler MUST evaluate the latest persisted opt-in value rather than the stale in-session account object

#### Scenario: Warm-up respects unsafe account states
- **WHEN** an account is paused, deactivated, rate-limited, quota-exceeded, or in an auth-refresh failure path
- **THEN** limit warm-up MUST NOT send traffic for that account

#### Scenario: Warm-up attempts are durable and deduplicated
- **WHEN** multiple refresh workers observe the same account/window/reset candidate
- **THEN** the database permits at most one persisted attempt for that tuple
- **AND** later refresh cycles skip that tuple after a prior attempt exists

