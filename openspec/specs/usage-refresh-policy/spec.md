# usage-refresh-policy Specification

## Purpose
Define how background usage refresh reacts to auth-like failures without permanently hammering bad accounts.
## Requirements
### Requirement: Usage refresh cools down repeated auth-like failures

Background usage refresh MUST apply a cooldown to accounts that repeatedly fail usage refresh with ambiguous `401` or `403` responses. Accounts in that cooldown window MUST be skipped until the cooldown expires or a later successful refresh clears it.

#### Scenario: Zero-capacity monthly primary does not keep free accounts rate-limited
- **GIVEN** a free-plan account whose persisted status is `rate_limited`
- **AND** its latest primary usage row is a zero-capacity non-5h window (for example a monthly upstream snapshot)
- **AND** its normalized quota state reports available monthly quota
- **WHEN** codex-lb derives account status for account summaries or proxy runtime state
- **THEN** the non-5h primary row is ignored for rate-limit recovery
- **AND** the account is treated as `active`
- **AND** downstream account views keep the monthly-only quota presentation

### Requirement: Usage refresh deactivates on clear deactivation signals

The system MUST deactivate accounts when usage refresh receives a permanent
account deactivation signal. Credential/session invalidation codes such as
`token_invalidated`, `token_expired`, and `app_session_terminated` MUST be
marked `reauth_required` instead of `deactivated`.

#### Scenario: Usage 401 app session terminated requires re-authentication

- **WHEN** usage refresh receives HTTP `401`
- **AND** the upstream error code is `app_session_terminated`
- **THEN** the account is marked `reauth_required`
- **AND** later usage refresh cycles skip that account until re-authentication

### Requirement: token_expired at the refresh boundary deactivates the account

The system MUST treat OAuth refresh credential-token or session errors as
permanent refresh-token/session failures. Codes include `token_expired`,
`app_session_terminated`, `invalid_grant`, `refresh_token_expired`,
`refresh_token_reused`, and `refresh_token_invalidated`. The affected account
MUST be marked `reauth_required` and removed from the routing pool until it is
re-authenticated.

#### Scenario: Refresh-time `app_session_terminated` is classified as permanent

- **WHEN** `classify_refresh_error("app_session_terminated")` is evaluated
- **THEN** it returns `True`

#### Scenario: Refresh-time `app_session_terminated` requires re-authentication

- **WHEN** `AuthManager.refresh_account` receives a
  `RefreshError("app_session_terminated", ..., is_permanent=True)` from
  `refresh_access_token`
- **THEN** the account is transitioned to `REAUTH_REQUIRED`
- **AND** the reason references the re-login requirement so the dashboard can
  surface it
- **AND** the account is no longer selected by the load balancer until it is
  re-authenticated

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

Background usage refresh SHALL reconcile persisted `rate_limited` and `quota_exceeded` accounts back to `active` after it writes fresh usage snapshots that prove the blocked window has recovered. This reconciliation SHALL be recovery-only and SHALL NOT promote `active` accounts into blocked statuses. For `rate_limited` accounts, recovery evidence SHALL come from the most recently recorded main-window row: when a post-block refresh no longer reports a short primary window and the last primary sample's own reset deadline has elapsed (or no primary sample exists), a fresh long-window row recorded after the block that still reports usage below `100%` proves recovery. While the last primary sample still claims an unexpired window (or omits reset metadata), or the newer long-window row is itself exhausted, primary freshness SHALL keep gating recovery.

#### Scenario: Scheduler recovers a stale rate-limited account from fresh primary usage
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** a later background usage refresh writes a fresh primary usage row recorded after the persisted block marker
- **AND** that primary usage row reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler recovers a rate-limited account that never had a primary row
- **WHEN** an account is persisted as `rate_limited` with no stored primary-slot row at all
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** a later background usage refresh records a fresh long-window row below `100%` after the persisted block marker
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler recovers a rate-limited account when upstream stops reporting the primary window
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** the last primary usage sample's own reset deadline has also elapsed
- **AND** a later background usage refresh records only a long-window usage row after the persisted block marker
- **AND** that long-window row reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Unexpired primary sample keeps gating recovery evidence
- **WHEN** an account is persisted as `rate_limited`
- **AND** the last primary usage sample predates the block but still claims an unexpired reset deadline
- **AND** a later refresh recorded only a fresh long-window row
- **THEN** the account stays `rate_limited` until fresh primary evidence arrives or the primary sample's reset deadline elapses

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
- **AND** no newer long-window row proves a post-block refresh
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
- **AND** no newer long-window row was recorded after the persisted block marker
- **THEN** the scheduler leaves the account blocked

#### Scenario: Scheduler skips recovery when the account row changed concurrently
- **WHEN** background usage refresh determines that a blocked account is recoverable
- **AND** the persisted account status or reset markers change before the scheduler writes recovery
- **THEN** the scheduler skips the stale recovery write

#### Scenario: Scheduler clears stale deactivation reasons on recovery
- **WHEN** background usage refresh recovers a `rate_limited` or `quota_exceeded` account to `active`
- **THEN** the scheduler writes `deactivation_reason` as `NULL`

### Requirement: Usage refresh does not trust elapsed reset windows

Background usage refresh MUST treat a latest usage row as stale when that row's `reset_at` timestamp is in the past, even when the row's `recorded_at` timestamp is still within the normal refresh interval — unless a strictly newer main-window row exists for the same account. When a later fetch recorded a sibling-window row after the elapsed row, upstream demonstrably no longer reports the elapsed window, and the newest row's freshness governs the account instead.

#### Scenario: Past reset_at bypasses freshness

- **GIVEN** the latest usage row was recorded within the normal refresh interval
- **AND** that row's `reset_at` timestamp has already elapsed
- **AND** no strictly newer main-window row exists for the account
- **WHEN** background usage refresh evaluates the account
- **THEN** the row is treated as stale
- **AND** codex-lb attempts a fresh upstream usage fetch

#### Scenario: Newer sibling row supersedes an elapsed primary row

- **GIVEN** an account whose latest primary row has an elapsed `reset_at`
- **AND** a later refresh recorded a secondary-window row within the normal refresh interval
- **WHEN** background usage refresh evaluates the account
- **THEN** the account is treated as fresh
- **AND** codex-lb does not fetch upstream usage again until the newest row ages out or its own reset elapses

#### Scenario: Secondary-only accounts are fresh by their newest row

- **GIVEN** an account with no primary-slot row at all because upstream omitted the short window
- **AND** a fresh secondary-window row within the normal refresh interval
- **WHEN** background usage refresh evaluates the account
- **THEN** the account is treated as fresh instead of fetching on every sweep visit

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

#### Scenario: Warm-up is not triggered by upstream reset_at timestamp jitter
- **GIVEN** limit warm-up is enabled globally and for an account
- **AND** the account's previous usage sample was exhausted
- **WHEN** background usage refresh records a newer sample whose `reset_at` advanced by less than 60 seconds (upstream timestamp jitter)
- **THEN** the system MUST NOT send a warm-up request for that account/window/reset tuple

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

#### Scenario: Staggered idle warm-up pre-starts rolling primary windows
- **GIVEN** limit warm-up and staggered idle warm-up are enabled globally
- **AND** multiple active accounts are opted into limit warm-up
- **AND** an opted-in account has a healthy idle short-window primary usage sample (any sample reporting a duration over 24 hours is not eligible) with `used_percent` at or below the configured `limit_warmup_idle_threshold_percent`
- **AND** no prior warm-up attempt places the account inside the configured cooldown
- **AND** the usage sample was refreshed for the current cycle
- **WHEN** background usage refresh evaluates that account inside its deterministic stagger slot
- **THEN** the system MUST attempt to send one minimal upstream warm-up request for that account's current rolling-window cycle, whose length is the account's observed primary window duration (defaulting to 300 minutes when duration metadata is missing)
- **AND** the system MUST NOT send another staggered idle warm-up for that same account/cycle tuple
- **AND** account slots MUST be spread deterministically across the account's rolling window so restarts do not align all opted-in accounts into the same phase

#### Scenario: Staggered idle warm-up is skipped for accounts with real usage
- **GIVEN** staggered idle warm-up is enabled globally
- **AND** an active opted-in account has a short-window primary usage sample with `used_percent` above the configured `limit_warmup_idle_threshold_percent`
- **WHEN** background usage refresh evaluates that account
- **THEN** the system MUST NOT send staggered idle warm-up traffic for that account

#### Scenario: Staggered idle warm-up remains opt-in
- **GIVEN** limit warm-up is enabled globally and for an account
- **AND** staggered idle warm-up is disabled
- **WHEN** background usage refresh observes an idle short-window primary sample that is not a reset-confirmed transition
- **THEN** limit warm-up MUST NOT send synthetic traffic for that idle sample

### Requirement: Operators can probe an account to wake the upstream limiter

The dashboard MUST expose an admin-only endpoint that sends a single minimal `responses.create` directly to upstream pinned to one account, bypassing load-balancer scoring, then immediately refreshes that account's `/wham/usage` snapshot. The endpoint MUST surface the before/after usage and account status so operators can verify whether the upstream limiter re-evaluated.

#### Scenario: Probe wakes the upstream limiter and refreshes usage state
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/probe`
- **AND** the account is `active`, `rate_limited`, or `quota_exceeded`
- **THEN** the service sends one `responses.create` request directly to `{upstream_base_url}/codex/responses` with `max_output_tokens=1`, `stream=true`, `store=false`
- **AND** the service triggers an immediate `UsageUpdater.refresh_accounts` for that account
- **AND** the response body carries `probe_status_code`, `primary_used_percent_before`, `primary_used_percent_after`, `secondary_used_percent_before`, `secondary_used_percent_after`, `account_status_before`, `account_status_after`

#### Scenario: Probe rejects hard-blocked accounts
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/probe`
- **AND** the account `status` is `paused` or `deactivated`
- **THEN** the endpoint responds `409` with code `account_not_probable`
- **AND** no upstream request is sent

#### Scenario: Dashboard exposes Force probe only for probeable statuses

- **WHEN** the dashboard renders account actions for an account
- **AND** the account `status` is `active`, `rate_limited`, or `quota_exceeded`
- **THEN** the dashboard exposes a Force probe action for that account
- **AND** invoking the action refreshes the account list, dashboard overview, projections, and that account's trends
- **BUT WHEN** the account `status` is `paused` or `deactivated`
- **THEN** the Force probe action is disabled or hidden

#### Scenario: Probe returns 404 for unknown account
- **WHEN** an operator POSTs to `/api/accounts/{account_id}/probe`
- **AND** no account with that id exists
- **THEN** the endpoint responds `404` with code `account_not_found`

### Requirement: Credit-backed usage remains selectable after quota windows fill

When deriving effective account status from upstream usage samples, the system MUST treat the latest credit metadata as an override for secondary quota-derived blocking state. If the latest usage sample with credit metadata reports `credits_has = true`, `credits_unlimited = true`, or `credits_balance > 0`, then secondary quota windows at `100%` MUST NOT by themselves make the account `quota_exceeded`. Primary-window exhaustion MUST keep `rate_limited` precedence even when credits are available.

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

#### Scenario: Credit-backed account remains rate-limited when primary window is exhausted

- **GIVEN** an account is otherwise routable
- **AND** its primary usage window reports `used_percent = 100`
- **AND** the newest usage sample with credit metadata reports a positive credit balance
- **WHEN** the load balancer derives account state
- **THEN** the derived status is `rate_limited`
- **AND** the reset guard points at the primary reset time

#### Scenario: Newer zero-credit sample removes the override

- **GIVEN** an older usage sample reports available credits
- **AND** a newer usage sample reports no credits and zero credit balance
- **WHEN** quota status is derived from usage
- **THEN** the newer zero-credit sample is authoritative
- **AND** a full quota window can still derive `rate_limited` or `quota_exceeded`

#### Scenario: Paused account is not reactivated by credits

- **GIVEN** an account is paused
- **AND** its newest usage sample reports available credits
- **WHEN** quota status is derived from usage
- **THEN** the account remains paused

### Requirement: Free-account quota normalizes to a monthly window

When upstream usage or rate-limit payloads report a single free-account quota window as `primary_window.limit_window_seconds == 2592000` with no `secondary_window`, the system SHALL normalize that payload as a monthly-only quota window rather than as a primary 5h window or a secondary 7d window.

#### Scenario: Monthly free-account payload becomes monthly-only
- **WHEN** usage refresh or rate-limit payload mapping receives `primary_window.limit_window_seconds = 2592000`
- **AND** `secondary_window` is `null`
- **THEN** the system records and exposes the quota as a monthly-only window
- **AND** it does not synthesize a 5h primary or 7d secondary window for that account

### Requirement: Free-account quota capacity applies only to the monthly window

The system SHALL treat the free-account monthly window as the only free-account quota capacity window for overview and summary calculations.

#### Scenario: Free account contributes only monthly quota capacity
- **WHEN** the system computes quota capacity for a free account with a normalized monthly-only window
- **THEN** the free account contributes capacity to the 30d monthly window
- **AND** the free account contributes zero 7d quota capacity

### Requirement: Weekly semantics are not inferred from the primary slot alone

The system SHALL NOT infer weekly secondary semantics solely because a primary-slot payload reports `limit_window_seconds == 604800`.

#### Scenario: Primary-slot weekly duration does not trigger implicit secondary mapping
- **WHEN** a payload includes a primary-slot window whose `limit_window_seconds` is `604800`
- **THEN** downstream interpretation is determined by the normalization rules for that account shape
- **AND** the system does not automatically treat that primary-slot payload as a secondary weekly window only because of that duration

### Requirement: Background usage refresh is staggered across accounts

Background usage refresh MUST distribute account refresh attempts across the
configured usage refresh interval instead of refreshing every eligible account
in one burst. Each scheduler slice MUST attempt at most one eligible account.
Over a full cycle, all eligible accounts SHOULD be considered once.

#### Scenario: Scheduler refreshes one account per slice

- **GIVEN** two active accounts are eligible for usage refresh
- **WHEN** the scheduler runs consecutive refresh slices
- **THEN** the first slice attempts one account
- **AND** the second slice attempts the other account
- **AND** cache invalidation for usage-derived routing state runs at the cycle
  boundary

#### Scenario: Unrefreshable accounts are skipped by scheduler rotation

- **GIVEN** one account is active
- **AND** one account is deactivated
- **AND** one account requires re-authentication
- **WHEN** the scheduler builds the refresh rotation
- **THEN** only the active account is considered

### Requirement: Usage refresh trusts recognized paid-plan transitions without workspace identity

Usage refresh MUST persist a stored account's `plan_type` change when
a usage payload that omits a `workspace_id` reports a recognized paid plan and
the stored plan is either `free` or another recognized paid plan (for example,
an upgrade from `free` to `plus` or from `plus` to `pro`). Because the usage
payload carries no independent account identifier and is fetched per-account
token, these transitions MUST be treated as legitimate plan changes rather than
account-slot identity mismatches. This requirement applies to scheduled usage
refresh and the forced refresh performed after an operator's Force probe.

A workspace-less usage payload MUST still be rejected, leaving the stored plan
unchanged, when it reports `free` or an unrecognized plan that differs from the
stored plan, since that is the signature of a degraded or wrong-identity usage
response. A usage payload whose `workspace_id` differs from the workspace the
account is bound to MUST continue to be rejected as a slot mismatch.

#### Scenario: Plus to Pro upgrade without a workspace is persisted

- **GIVEN** an active account with stored `plan_type` `plus` and no `workspace_id`
- **WHEN** background usage refresh returns a payload with `plan_type` `pro` and no `workspace_id`
- **THEN** the account's stored `plan_type` becomes `pro` and the usage sample is written

#### Scenario: Force probe persists a Free to Plus upgrade

- **GIVEN** an active account with stored `plan_type` `free` and no `workspace_id`
- **WHEN** Force probe refreshes usage and the payload reports `plan_type` `plus`
- **THEN** the account's stored `plan_type` becomes `plus` without reauthentication

#### Scenario: Free downgrade without a workspace is rejected

- **GIVEN** an active account with stored `plan_type` `business` and no `workspace_id`
- **WHEN** background usage refresh returns a payload with `plan_type` `free` and no `workspace_id`
- **THEN** the account's stored `plan_type` stays `business` and no usage mutation is applied

#### Scenario: Conflicting workspace identity is rejected

- **GIVEN** an active account bound to `workspace_id` `ws_team`
- **WHEN** background usage refresh returns a payload whose `workspace_id` is `ws_other`
- **THEN** the account is left unchanged and no usage mutation is applied

### Requirement: Codex usage exposes reset-credit availability

codex-lb SHALL include upstream reset-credit availability on Codex usage
responses when ChatGPT usage identity validation returns earned usage limit
reset credits, without altering aggregate usage-window semantics.

#### Scenario: Usage response carries reset credits

- **GIVEN** a registered active `chatgpt-account-id`
- **AND** upstream `/wham/usage` returns `rate_limit_reset_credits.available_count`
- **WHEN** the caller requests `/api/codex/usage` with a ChatGPT bearer token
- **THEN** codex-lb returns a successful Codex usage payload
- **AND** the top-level `rate_limit_reset_credits.available_count` equals the upstream value

### Requirement: Codex usage can consume upstream reset credits

codex-lb SHALL expose a Codex-compatible endpoint for consuming one upstream
usage limit reset credit. The endpoint SHALL require ChatGPT caller identity,
forward the caller's bearer token and `chatgpt-account-id`, preserve the
caller-provided `redeem_request_id`, and return the upstream consume outcome.

#### Scenario: Reset credit consume succeeds

- **GIVEN** a registered active `chatgpt-account-id`
- **AND** upstream reset-credit consume returns `code: reset`
- **WHEN** the caller posts to `/api/codex/rate-limit-reset-credits/consume` with `redeem_request_id`
- **THEN** codex-lb returns `code: reset`
- **AND** codex-lb force-refreshes the matching account usage snapshot
- **AND** the force-refresh runs even when background usage refresh scheduling is disabled

#### Scenario: API-key caller cannot consume ChatGPT reset credits

- **GIVEN** a codex-lb API key caller without ChatGPT caller identity
- **WHEN** the caller posts to `/api/codex/rate-limit-reset-credits/consume`
- **THEN** codex-lb rejects the request as unauthenticated for ChatGPT reset credits

#### Scenario: Empty redemption id is rejected

- **GIVEN** a registered active `chatgpt-account-id`
- **WHEN** the caller posts to `/api/codex/rate-limit-reset-credits/consume` with an empty `redeem_request_id`
- **THEN** codex-lb rejects the request without forwarding it upstream

### Requirement: Account details expose reset-credit availability

codex-lb SHALL expose upstream usage limit reset-credit availability for a
selected dashboard account without creating local reset-credit accounting.

#### Scenario: Dashboard account detail shows reset credits

- **GIVEN** a registered active account with a `chatgpt-account-id`
- **AND** upstream `/wham/usage` returns `rate_limit_reset_credits.available_count`
- **WHEN** the dashboard requests the account's reset-credit summary
- **THEN** codex-lb returns the selected account id
- **AND** `rate_limit_reset_credits.available_count` equals the upstream value

### Requirement: Dashboard account details can consume reset credits

codex-lb SHALL expose a dashboard write-authorized endpoint for consuming one
upstream usage limit reset credit for a selected account. The endpoint SHALL use
the selected account's stored ChatGPT access token and `chatgpt-account-id`,
generate a non-empty `redeem_request_id`, return the upstream consume outcome,
and force-refresh the matching account usage snapshot after successful or
idempotently successful consume.

#### Scenario: Dashboard reset credit consume succeeds

- **GIVEN** a registered active dashboard account with a `chatgpt-account-id`
- **AND** upstream reset-credit consume returns `code: reset`
- **WHEN** the dashboard posts to the selected account reset-credit consume endpoint
- **THEN** codex-lb forwards a non-empty `redeem_request_id` upstream
- **AND** codex-lb returns `code: reset`
- **AND** codex-lb force-refreshes the matching account usage snapshot
- **AND** the force-refresh runs even when background usage refresh scheduling is disabled

#### Scenario: Read-only dashboard cannot consume reset credits

- **GIVEN** a read-only dashboard session
- **WHEN** the dashboard posts to the selected account reset-credit consume endpoint
- **THEN** codex-lb rejects the request without consuming a reset credit

### Requirement: Usage refresh is account-slot scoped

Usage refresh MUST write usage and change account status only for the credential slot being refreshed. It MUST NOT apply a payload that proves a different workspace identity to the target account. Workspace-less payloads that report a recognized paid-plan transition are governed by the paid-plan transition requirement and MUST NOT be treated as slot mismatches.

#### Scenario: Mismatched workspace payload is ignored

- **GIVEN** an account has stored workspace identity
- **WHEN** usage refresh receives a payload for a different workspace
- **THEN** no usage rows are written for the account
- **AND** the account status, plan type, workspace metadata, and seat type are not changed

#### Scenario: Unknown workspace plan mismatch is non-destructive

- **GIVEN** an account has no stored workspace identity
- **WHEN** usage refresh receives a payload whose plan type is `free` or unrecognized and conflicts with the stored non-unknown plan
- **THEN** no usage rows are written for the account
- **AND** the account status and plan type are not changed

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

### Requirement: Aggregated rate-limit surfaces expire elapsed windows

Aggregated downstream rate-limit surfaces — the pooled `x-codex-{window}-*` response headers and the rate-limit status payload — SHALL treat a usage row whose `reset_at` has elapsed as a reset window (`0%` used, no reset timestamp) when computing pooled summaries and availability. These surfaces SHALL NOT report `limit_reached` from elapsed samples alone.

#### Scenario: Elapsed primary rows stop freezing pooled headers

- **GIVEN** upstream stopped reporting a primary window and every stored primary row has an elapsed `reset_at`
- **WHEN** pooled rate-limit headers are computed
- **THEN** the pooled primary used percentage reflects the expired rows as `0%`
- **AND** no elapsed reset timestamp is emitted for the primary window

#### Scenario: Elapsed samples do not report limit_reached

- **GIVEN** every account's stored primary row reports `100%` used with an elapsed `reset_at`
- **AND** fresh secondary rows report usage below `100%`
- **WHEN** the rate-limit status payload is computed
- **THEN** `limit_reached` is false
- **AND** the primary window is omitted from the payload instead of advertising the elapsed reset

