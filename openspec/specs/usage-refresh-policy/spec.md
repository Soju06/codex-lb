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

Before persisting a permanent refresh failure, the system MUST re-read the
account's token material from the database with a real SELECT that bypasses
session identity caches, MUST NOT downgrade the account when the refresh token
rotated after the failed attempt began (returning the rotated tokens instead),
and MUST apply the status downgrade with a compare-and-set conditioned on the
freshly observed account state including the refresh-token ciphertext, so a
concurrent re-authentication or rotation — even one that leaves
status/reason/reset untouched — is never overwritten.

When that status compare-and-set misses, a ciphertext change MUST NOT by itself
be treated as a rotation to defer to: because token ciphertext is
non-deterministic, a concurrent re-authentication or import can re-encrypt the
SAME refresh-token plaintext to different bytes between the fresh re-read and
the write. The system MUST compare the freshly observed refresh-token material
against the material this attempt exchanged by decrypted-plaintext fingerprint.
When the fingerprint is genuinely different the system MUST adopt the stored row
without downgrading, and MUST return those rotated tokens to the caller (rather
than returning the success/no-op sentinel that lets the caller re-raise the
original permanent error) — whether the genuine difference is observed at the
initial fresh re-read or only after a status compare-and-set miss. Re-raising in
the compare-and-set-miss window would send proxy callers into the permanent-failure
path (for example `LoadBalancer.mark_permanent_failure()`), whose status write is
NOT guarded by this refresh-token compare-and-set, so it would clobber the peer's
valid rotation with `reauth_required` and tear down sessions for an account a peer
just repaired. When the fingerprint is unchanged — the account is still
holding the very material that just failed permanently — the system MUST re-read
and retry the compare-and-set against the freshly observed ciphertext (bounded)
so the downgrade lands, rather than skipping the status write and leaving the
account active with dead credentials.

When the bounded status-downgrade compare-and-set is EXHAUSTED without ever
landing — a sustained same-plaintext re-encryption storm the system cannot win
an atomic compare-and-set window against, with no genuinely different peer
rotation ever observed — the system MUST NOT return the success/no-op sentinel
that re-raises the original permanent error, and MUST NOT fall back to an
unconditional (unguarded) status write. Because the system could not
authoritatively persist `reauth_required` under the ciphertext guard, re-raising
the permanent error would send proxy callers into the permanent-failure path (for
example `LoadBalancer.mark_permanent_failure()`), whose status write is NOT
guarded by this refresh-token compare-and-set — so in the storm, or if a genuine
peer re-authentication/import rotation lands after the final re-read but before
that unguarded write, it would clobber a repaired account with `reauth_required`,
the exact clobber the compare-and-set guards prevent. The system MUST instead
raise a transient (non-permanent, transport-level) refresh error that is not
recorded in the permanent-failure cooldown, so the caller retries the whole
refresh once the contention clears rather than running the unguarded permanent
mark. This transient escalation applies ONLY to contention-driven exhaustion
while the account still holds the failed material; a status compare-and-set that
SUCCEEDS still stands as a real permanent failure, and a genuinely different peer
rotation observed on re-read is still adopted as a repair.

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

#### Scenario: Concurrent rotation loser receives refresh_token_reused

- **GIVEN** another replica rotated the account's refresh token and committed
  while this replica's exchange with the old token was in flight
- **WHEN** this replica's exchange fails with `refresh_token_reused`
- **THEN** no `reauth_required` write occurs
- **AND** this replica returns the rotated tokens from the database

#### Scenario: Status CAS misses on a re-encryption of the same failing token

- **GIVEN** this replica's exchange failed permanently and the account still
  holds the same refresh-token plaintext that failed
- **AND** a concurrent re-authentication/import re-encrypted that SAME plaintext
  to different ciphertext between the fresh re-read and the status CAS, so the
  CAS misses while status/reason/reset are unchanged
- **WHEN** the guard re-reads and finds the refresh-token fingerprint unchanged
- **THEN** it retries the status CAS against the freshly observed ciphertext and
  lands the `reauth_required` downgrade
- **AND** it does not leave the account active with the dead credentials

#### Scenario: Peer rotation lands in the status-CAS-miss window

- **GIVEN** this replica's exchange failed permanently and the fresh re-read
  still showed the same failing refresh-token material
- **AND** a concurrent re-authentication/rotation committed a genuinely
  different refresh token between that fresh re-read and the status CAS, so the
  CAS misses
- **WHEN** the guard re-reads and finds the refresh-token fingerprint now
  genuinely different from the material this attempt exchanged
- **THEN** it adopts the stored row and returns the peer's rotated tokens to the
  caller
- **AND** no `reauth_required` write occurs and the original permanent error is
  not re-raised, so the caller does not enter the permanent-failure path for the
  already-repaired account

#### Scenario: Status CAS exhausts on a same-plaintext re-encryption storm

- **GIVEN** this replica's exchange failed permanently and the account still
  holds the same refresh-token plaintext that failed
- **AND** a sustained concurrent re-encryption of that SAME plaintext keeps
  shifting the observed ciphertext, so every conditional status compare-and-set
  misses through the bounded retry budget with no genuinely different peer
  rotation ever observed
- **WHEN** the bounded status-downgrade compare-and-set is exhausted without ever
  landing
- **THEN** the guard raises a transient, non-permanent (transport-level) refresh
  error that is not recorded in the permanent-failure cooldown
- **AND** it does not write `reauth_required` and does not fall back to an
  unconditional status write
- **AND** the original permanent error is not re-raised, so the caller retries
  the whole refresh rather than running the unguarded
  `LoadBalancer.mark_permanent_failure()` path that could clobber a concurrent
  peer rotation

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

Auth Guardian SHALL use the existing leader-election mechanism so only the elected replica performs proactive refresh work. When leader election is disabled, the guardian MUST detect multi-replica operation dynamically from live bridge ring membership (members with a heartbeat within the staleness threshold) in addition to the static instance ring, MUST skip the refresh pass when more than one live replica is detected, and MUST log a warning identifying the leader-election setting.

#### Scenario: Replica is not leader

- **GIVEN** leader election is enabled
- **AND** the current replica does not acquire leadership
- **WHEN** Auth Guardian wakes
- **THEN** the scheduler skips refresh work for that pass

#### Scenario: Dynamically registered replicas without leader election

- **GIVEN** two replicas registered in `bridge_ring_members` with live heartbeats
- **AND** the static instance ring is empty
- **AND** leader election is disabled
- **WHEN** an Auth Guardian tick runs on either replica
- **THEN** the guardian performs no refresh work
- **AND** logs a warning identifying the leader-election setting

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

### Requirement: Cross-replica token refresh serialization

Before any upstream OAuth token exchange for an account, the system MUST acquire that account's row in `account_refresh_claims` via a conditional upsert that succeeds only when no unexpired claim by another claimant exists; the upsert MUST be atomic on both PostgreSQL (ON CONFLICT row lock) and SQLite (single-writer lock). After acquiring, the system MUST re-read the account's refresh-token material fresh from the database (bypassing session identity caches) and MUST skip the upstream exchange when the material has rotated since the refresh was requested, adopting the stored tokens instead. Claims MUST carry an expiry covering all work performed under the claim (TTL at least the refresh-admission wait timeout plus twice the refresh HTTP timeout, because the claim is held across the admission wait and the OAuth exchange) so a crashed claimant cannot block refresh indefinitely while a healthy claimant cannot lose its claim mid-work, MUST be released after the refreshed tokens are persisted, and MUST NOT be held as an open database transaction or lock across upstream network I/O. The claim expiry — BOTH the stored `claim_expires_at` AND the takeover predicate that treats an existing claim as expired — MUST be evaluated on the DATABASE server clock (`clock_timestamp()`/`now()` on PostgreSQL; in-statement `strftime(..., 'now')` on SQLite), never against a replica-local Python wall-clock instant captured before the statement executes, so inter-replica clock skew can never let one replica treat another replica's still-live claim as expired and steal it (which would let two replicas exchange the same single-use refresh token concurrently). This mirrors the clock-domain guarantee of the scheduler leader election. When the claim TTL is not explicitly configured, the system MUST derive its default to at least this floor from the related timeout settings, so a deployment that predates the claim-TTL setting but raised the refresh or admission timeouts still starts up (never crashing during settings construction against a fixed default); the system MUST reject only an explicitly configured TTL below the floor. The claimant identity MUST remain unique per OS process even when the configured instance id exceeds the stored column width (truncate the instance-id portion, never the per-process suffix). The per-process suffix MUST be derived per OS process and resolved at claim-build time (for example incorporating `os.getpid()`), never frozen at module import: in pre-fork/multi-worker deployments a module imported before the fork boundary MUST NOT hand every forked child an identical suffix, so two sibling workers sharing one instance id build DISTINCT claimant identities (and thus distinct `claimed_by` values) rather than both satisfying the re-entrant claim upsert and refreshing the single-use token concurrently. The suffix MUST also remain stable across repeated calls within a single process so genuine same-process re-entrant claims still match. The same fork-safety MUST hold for the coordinator that composes claims: a process-default/auto-derived claimant identity MUST NOT be frozen when the coordinator is constructed (the process-default coordinator is commonly built during preload/startup, before a pre-fork server forks its workers, and a frozen identity would be inherited identically by every child). It MUST instead be resolved per OS process at use time so two forked children build DISTINCT claimant identities; a claimant identity that a caller explicitly injects MUST remain stable and unchanged (including across a fork), and repeated reads within one process MUST stay stable.

After acquiring the claim and re-reading the account fresh, and BEFORE starting a new upstream exchange, the system MUST honor a TERMINAL account status committed by a prior claim holder. When the fresh row's refresh-token fingerprint is UNCHANGED from the material the refresh was requested with (so no peer rotation repaired it) AND the fresh row's status is terminal (`REAUTH_REQUIRED` or `DEACTIVATED`) — for example a prior holder that hit a permanent `invalid_grant`, or the safe-terminal persist-conflict path that flags `REAUTH_REQUIRED` while leaving the consumed token stored — the system MUST NOT re-exchange that unchanged consumed/dead token; it MUST instead surface the terminal state as a PERMANENT refresh failure (fail closed), so a waiter that wins the released claim cannot blindly retry the consumed token and generate another permanent failure for an account a peer already removed from rotation. This decision MUST use the FRESH re-read status and fingerprint, never the stale selection snapshot, and MUST compose with the adopt-vs-exchange logic so that a CHANGED fingerprint (a peer genuinely re-authenticated/rotated and repaired the account) still causes the system to ADOPT the rotated stored tokens and proceed rather than treating a repaired account as terminal.

The claim release runs after the token update has been persisted (in a cleanup/`finally` path). A failure of the release itself (a transient DB error such as a SQLite lock past the busy timeout or a dropped Postgres connection) MUST NOT mask an otherwise successful refresh: the release MUST be retried briefly and then logged and suppressed, never propagated over a successful `_perform_refresh`/adoption return value, because the committed rotation is already durable and the stale claim harmlessly expires by its TTL. Suppression MUST be scoped to the release operation's own errors only: an exception raised by the refresh body itself MUST still propagate unchanged.

Claim ownership MUST be per-refresh, not process-wide: the stored claim identity MUST combine the claimant (replica/process) identity with a per-refresh owner token derived from the refresh-token material being exchanged (its fingerprint). The re-entrant same-owner takeover that lets a crashed refresh reclaim its own live claim MUST match only when BOTH the claimant AND the owner token are identical; a release MUST delete only the exact composed claim. Consequently, when two refreshes for the same account run in one process with different token fingerprints (for example a re-auth/import lands while an older forced refresh is still in flight), the second refresh MUST contend for the claim (wait until the first releases or the claim expires) rather than re-entering the first refresh's live claim, and neither refresh's release MAY delete the other's claim. The composed claim identity MUST fit the stored column width without truncating either the per-process suffix or the owner token.

After a successful upstream exchange, the system MUST persist the newly issued tokens with a compare-and-set conditioned on the refresh-token ciphertext observed in the immediately-preceding read. There MUST be NO unconditional token write anywhere in the persistence path: EVERY persist — including the final/exhaustion persist — MUST be a compare-and-set guarded on that observed ciphertext (`WHERE refresh_token_encrypted == :observed`). Because that comparison is atomic in the database, there is no read→write gap: if anything changed the row after the read (a non-deterministic re-encryption of the same plaintext OR a genuine peer rotation) the guarded write MISSES and clobbers nothing.

When that compare-and-set misses, the system MUST NOT assume any ciphertext change is a newer rotation: it MUST decide on the DECRYPTED refresh-token PLAINTEXT, never on the non-deterministic ciphertext (a concurrent re-authentication or import can re-encrypt the same plaintext to different bytes). It MUST re-read and compare the freshly observed stored plaintext against the plaintext this attempt exchanged FROM: (i) when the stored material is a genuinely different refresh token a peer rotated, so the system MUST adopt the stored row without persisting its own result and MUST NOT overwrite it; (ii) when the stored material is the same plaintext merely re-encrypted, the system MUST retry the ciphertext-guarded compare-and-set against the freshly observed ciphertext (bounded) so its own single-use rotation is persisted rather than discarded — it MUST NOT give up while the consumed token is still what is stored; (iii) only when the stored plaintext cannot be decrypted/compared MUST the system raise a transient (non-permanent) refresh error that is not recorded in the permanent-failure cooldown.

When the bounded guarded retries are exhausted without ever landing (a sustained same-plaintext re-encryption storm the system cannot win an atomic compare-and-set window against, with no genuinely different peer rotation observed) — OR the claim/caller deadline cut the retry loop mid-storm — the system MUST NOT immediately raise a transient error and drop the freshly rotated single-use token. It MUST first run a DEDICATED, small, bounded final-persist retry loop (a few guarded compare-and-set attempts with tiny backoff) that is DELIBERATELY SEPARATE from the claim/caller deadline: persisting a valid rotated token is worth a few extra milliseconds over budget, because giving up strands the account holding the already-consumed token. Each dedicated attempt is still a ciphertext-guarded compare-and-set keyed on the freshly re-read ciphertext (adopt a genuinely different peer plaintext, retry a same-plaintext re-encryption against the newly observed ciphertext); because any ciphertext change means a writer committed and no realistic writer re-encrypts the same consumed token in a tight loop, this lands within a couple of attempts in every realistic case. Only if those dedicated final retries are ALL exhausted while the stored material stays the already-consumed token (a truly pathological same-plaintext storm, or undecryptable stored material) does the system reach a SAFE TERMINAL OUTCOME: it MUST NOT surface a bare transient `token_persist_conflict` that releases the claim and lets a later blind retry re-exchange the still-stored consumed token into an `invalid_grant`/reauth PERMANENT knockout of an otherwise-healthy account. It MUST instead FAIL CLOSED by flagging the account `REAUTH_REQUIRED` through the SAME ciphertext-guarded status compare-and-set (keyed on the last-observed ciphertext), so the dead stored token is explicitly surfaced to operators (a recoverable, operator-visible state — the database genuinely holds a dead token) rather than left silently holding a consumed token that a blind retry would knock out; a genuine peer rotation that lands in the guard window is still ADOPTED (never clobbered), and only if even that guarded status write keeps missing on unchanged material through its own bounded budget MAY the system fall back to the transient (non-permanent) `token_persist_conflict` as the last resort (kept out of the permanent-failure cooldown). The system MUST NOT fall back to an unconditional write at any point.

Removing the unconditional write resolves — structurally, not by picking a side — the long-standing tension between never dropping the freshly rotated token and never clobbering a genuine peer rotation: (A) the freshly rotated token is not dropped, because when the stored plaintext is confirmed to be the same consumed token the system keeps pushing its new token in via the guarded retry (including the dedicated final-persist retries) rather than giving up; and (B) a genuine peer rotation is never clobbered, because every write is guarded, so a rotation that lands in the former read→write gap now simply causes a miss and is ADOPTED on re-read. The dedicated final-persist retries close the irreducible trilemma corner (never-clobber vs never-drop vs bounded-time) in every realistic case; the only residual outcome in the truly pathological corner is the SAFE TERMINAL `REAUTH_REQUIRED` flag (recoverable, never a permanent knockout, never a clobber), with the transient `token_persist_conflict` demoted to a last resort behind even the guarded status write.

#### Scenario: Two replicas force-refresh the same account concurrently

- **GIVEN** two replicas hold the same refresh-token material for one account
- **WHEN** both trigger a forced token refresh concurrently (for example after a shared upstream 401)
- **THEN** exactly one upstream token exchange occurs
- **AND** the account remains `active`
- **AND** both replicas end up with the rotated token material
- **AND** the account's sticky sessions and bridge sessions are untouched

#### Scenario: Claimant crashes mid-refresh

- **GIVEN** a replica acquired the refresh claim for an account and crashed before releasing it
- **WHEN** another replica attempts to refresh the account after the claim TTL has elapsed
- **THEN** the claim acquisition succeeds and the refresh proceeds

#### Scenario: Timeout-only config predating the claim TTL setting still boots

- **GIVEN** a deployment that raised the refresh HTTP timeout or the admission wait timeout above the values that keep the fixed 30s default above the floor
- **AND** that deployment does not explicitly configure the claim TTL
- **WHEN** settings are constructed
- **THEN** construction succeeds with a claim-TTL default derived to at least the floor (admission wait plus twice the refresh timeout)
- **AND** an explicitly configured claim TTL below the floor is still rejected

#### Scenario: Two refreshes in one process with different fingerprints contend

- **GIVEN** a refresh for an account is in flight in a process, holding the account's claim under one refresh-token fingerprint
- **WHEN** a second refresh for the same account starts in the same process with a different refresh-token fingerprint (for example after a re-auth/import)
- **THEN** the second refresh does NOT re-enter the live claim and instead contends (waits until the first releases or the claim expires)
- **AND** releasing either refresh's claim does not delete the other refresh's claim

#### Scenario: Process-default coordinator built before a pre-fork boundary

- **GIVEN** the process-default refresh-claim coordinator is constructed during preload/startup (before a pre-fork server forks its workers)
- **WHEN** two forked children each read their coordinator's claimant identity for the same account and refresh-token owner
- **THEN** each child yields a DISTINCT claimant identity and a distinct composed `claimed_by` (the auto-derived identity is resolved per OS process, never frozen at construction)
- **AND** a claimant identity explicitly injected by a caller stays unchanged across the fork
- **AND** repeated reads within one process return the same claimant identity

#### Scenario: Claim release failure does not mask a successful refresh

- **GIVEN** a replica won the refresh claim, completed the upstream exchange, and persisted the rotated tokens
- **WHEN** releasing the claim in the cleanup path raises a transient DB error (for example a SQLite lock past the busy timeout or a dropped Postgres connection)
- **THEN** the release is retried briefly and then logged and suppressed
- **AND** the caller still receives the successfully refreshed account (the release error never replaces the return value)
- **AND** the stale claim is left to expire by its TTL

#### Scenario: Claim release failure does not swallow a refresh-body error

- **GIVEN** a replica won the refresh claim and its upstream exchange raised a refresh error
- **WHEN** releasing the claim in the cleanup path also raises a transient DB error
- **THEN** the original refresh-body error propagates to the caller unchanged (the release error is suppressed, not the body error)

#### Scenario: Winner adopts a rotation that landed before its claim

- **GIVEN** a replica acquires the refresh claim for an account
- **AND** the freshly re-read refresh-token material differs from the material the refresh was requested with
- **WHEN** the replica proceeds
- **THEN** it returns the stored tokens without any upstream token exchange

#### Scenario: Waiter honors a prior holder's terminal status on an unchanged token

- **GIVEN** a prior claim holder finished by committing a terminal status (`REAUTH_REQUIRED` from a permanent `invalid_grant`, or the safe-terminal persist-conflict path) WITHOUT rotating `refresh_token_encrypted`, then released the claim
- **AND** a waiter subsequently wins the released claim with a stale snapshot of the same refresh token
- **WHEN** the waiter re-reads the account fresh and finds the refresh-token fingerprint UNCHANGED and the status terminal
- **THEN** it does NOT run a second upstream exchange of the consumed/dead token
- **AND** it surfaces the terminal state as a PERMANENT (non-transport) refresh failure, failing closed
- **AND** the account remains `REAUTH_REQUIRED` and the stored token is unchanged

#### Scenario: Waiter adopts a peer rotation that repaired a terminal account

- **GIVEN** a prior claim holder flagged the account `REAUTH_REQUIRED` on the old token
- **AND** a peer then genuinely re-authenticated, rotating `refresh_token_encrypted` (fingerprint changed) and clearing the status
- **WHEN** a waiter wins the claim, re-reads the account fresh, and finds the refresh-token fingerprint CHANGED
- **THEN** it adopts the peer's rotated stored tokens and proceeds without any upstream exchange
- **AND** it does NOT treat the repaired account as terminal

#### Scenario: Persistence compare-and-set misses on a re-encryption of the same token

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** a concurrent re-authentication/import re-encrypted the SAME refresh-token plaintext to different ciphertext, so the persistence compare-and-set misses
- **WHEN** the replica re-reads the stored material and finds its refresh-token fingerprint unchanged from the material it exchanged
- **THEN** it retries the compare-and-set against the freshly observed ciphertext and persists its own newly issued tokens
- **AND** it does not adopt the re-encrypted, already-consumed token

#### Scenario: Persistence compare-and-set stabilizes on the second dedicated final-persist attempt

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** the guarded persistence compare-and-set keeps missing on a same-plaintext re-encryption storm through the whole bounded retry budget AND the FIRST dedicated final-persist attempt
- **AND** the ciphertext then STABILIZES so the SECOND dedicated final-persist attempt's guarded compare-and-set (keyed on the last-observed ciphertext) can land
- **WHEN** the replica runs the dedicated final-persist retries (which are separate from the claim/caller deadline)
- **THEN** the second dedicated attempt persists the freshly rotated token and evicts the consumed one
- **AND** NO transient `token_persist_conflict` is raised, the token is not dropped, and the account is NOT flagged `REAUTH_REQUIRED`
- **AND** every attempt was a guarded compare-and-set, so nothing was clobbered

#### Scenario: Persistence compare-and-set never lands on a same-plaintext re-encryption storm

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** the guarded persistence compare-and-set keeps missing on a sustained same-plaintext re-encryption storm until BOTH the bounded retry budget AND the dedicated final-persist retries are exhausted, with no genuinely different peer rotation ever observed
- **WHEN** the replica still cannot win an atomic compare-and-set window after the dedicated final-persist retries
- **THEN** it reaches the SAFE TERMINAL OUTCOME: it flags the account `REAUTH_REQUIRED` through the SAME ciphertext-guarded status compare-and-set (keyed on the last-observed ciphertext), so the account is explicitly surfaced for re-auth (recoverable, operator-visible — the database genuinely holds a dead, already-consumed token) rather than left silently holding a consumed token
- **AND** it MUST NOT surface a bare transient `token_persist_conflict` that releases the claim and lets a later blind retry re-exchange the still-stored consumed token into an `invalid_grant`/reauth PERMANENT knockout of the healthy account
- **AND** a genuine peer rotation observed while flagging is still ADOPTED (never clobbered), and only if even the guarded status write keeps missing on unchanged material through its own bounded budget MAY the transient `token_persist_conflict` be raised as a last resort (kept out of the permanent-failure cooldown)
- **AND** it never falls back to an unconditional write, so no write can clobber a rotation that lands in a read→write gap

#### Scenario: Persistence compare-and-set misses on a genuine peer rotation in the read→write gap

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** its confirming re-read observed the same refresh-token plaintext it exchanged FROM (only re-encrypted)
- **AND** a genuinely different peer rotation lands AFTER that plaintext-confirming read but BEFORE the persist
- **WHEN** the replica issues its ciphertext-guarded write and it MISSES the peer's ciphertext, then re-reads and decrypts the stored plaintext and finds it is a genuinely different valid token
- **THEN** it adopts the peer's stored tokens without persisting its own result
- **AND** because the write was guarded it clobbered nothing, so the peer's newer valid tokens are never overwritten with the already-consumed material

#### Scenario: Persistence compare-and-set exhausts and the stored plaintext cannot be compared

- **GIVEN** a replica completed a successful upstream token exchange and holds the newly issued single-use tokens
- **AND** the persistence compare-and-set is exhausted and the stored refresh-token material cannot be decrypted for a plaintext comparison
- **WHEN** the replica cannot prove whether the stored material is the same consumed token or a genuine peer rotation
- **THEN** it raises a transient, non-permanent refresh error that is not recorded in the permanent-failure cooldown, so the caller retries the whole refresh once the contention clears rather than risking a clobber

#### Scenario: Persistence compare-and-set misses on a genuine peer rotation

- **GIVEN** a replica completed a successful upstream token exchange
- **AND** a peer committed a genuinely different refresh token, so the persistence compare-and-set misses
- **WHEN** the replica re-reads the stored material and finds its refresh-token fingerprint changed
- **THEN** it adopts the peer's stored tokens without persisting its own result

#### Scenario: Benign claim contention and post-exchange persist conflict are classified distinctly

- **GIVEN** a `RefreshError(code="refresh_claim_timeout", transport_error=True)` (benign: a peer holds the claim, no exchange happened) and a `RefreshError(code="token_persist_conflict", transport_error=True)` (post-exchange: the single-use token was consumed but its rotation could not be persisted)
- **WHEN** the classification predicates evaluate each
- **THEN** `is_refresh_claim_contention` is true ONLY for `refresh_claim_timeout`, `is_refresh_persist_conflict` is true ONLY for `token_persist_conflict`/`status_downgrade_conflict`, and `is_transient_refresh_contention` is true for BOTH
- **AND** a genuine `RefreshError(code="transport_error")` satisfies NONE of the three predicates
- **AND** both categories yield the same external outcome (retryable `upstream_unavailable`, never cached, no account-health penalty), but a post-exchange persist conflict is logged/observed distinctly from benign contention

#### Scenario: Retry after a post-exchange persist conflict re-exchanges rather than reusing the stored token

- **GIVEN** a refresh raised the transient `token_persist_conflict` (its `transport_error=True` keeps it out of the singleflight failure cache)
- **WHEN** the caller retries the refresh
- **THEN** the retry re-runs the WHOLE refresh (re-acquire the claim, fresh re-read, fresh upstream OAuth exchange) rather than reusing a cached result or reusing the possibly-consumed stored token
- **AND** the transient conflict MUST NOT be treated as an immediate permanent knockout without that fresh re-exchange attempt

### Requirement: Refresh claim losers wait bounded and never degrade account status

A process that fails to acquire the refresh claim MUST wait by polling within a bounded deadline (configurable cap, additionally bounded by the caller's refresh timeout budget). Each per-iteration poll sleep MUST be capped to the time remaining before that deadline (the smaller of the configured poll interval and the remaining budget), and when no time remains the loop MUST stop polling and fail fast with the transient claim-timeout error; a shielded refresh task MUST NOT sleep a full poll interval past the caller's deadline, because doing so would overrun the caller budget while still holding its repo session and the inflight singleflight entry that later callers join. When it observes rotated refresh-token material it MUST return the stored tokens without an upstream call. When the deadline elapses it MUST fail with a transient (non-permanent) refresh error that is not recorded in the permanent-failure cooldown, and it MUST NOT write `reauth_required` or `deactivated`, so token-refresh recovery fails over to another account instead of blocking.

When a process DOES win the claim (either immediately or after waiting on a foreign claim that released), and a caller refresh-timeout budget is in effect, the process MUST recompute the remaining budget (the caller's original deadline minus the elapsed wait) before starting the upstream OAuth exchange. Because the singleflight refresh body is shielded from caller cancellation and outlives a cancelled caller, it MUST NOT proceed into the exchange with the caller's ORIGINAL timeout budget still in force after a long wait: it MUST either fail fast with the transient (non-permanent) claim-timeout error when no budget remains, or cap the exchange timeout to the remaining budget, so a claim wait can never be followed by a full-budget exchange that overruns the request deadline and keeps the repo session and singleflight entry pinned past the budget.

The ENTIRE window during which the claim winner holds the cross-replica refresh claim MUST be bounded by the caller's remaining budget (when a budget is in effect), not merely the OAuth HTTP exchange. In particular, before the exchange the claim winner acquires token-refresh admission from the concurrency gate, and that admission acquire MUST be capped by the remaining budget: on a saturated token-refresh admission semaphore the wait for a slot (otherwise up to the configured admission wait timeout) MUST NOT exceed the caller's remaining budget while the claim is held. When the budget is already exhausted at admission time, or the admission wait would elapse it, the winner MUST fail fast with the transient (non-permanent) claim-timeout error (releasing the claim) rather than continuing to wait for a slot. After admission is acquired, the exchange-timeout cap MUST reflect the budget that actually remains (the admission wait counts against the budget), so admission wait plus exchange together cannot exceed the caller's budget and cannot hold the claim — blocking peer replicas — past the request deadline.

The POST-exchange persistence section — the token-persist compare-and-set loop and the permanent-failure status-downgrade compare-and-set loop — also runs while the claim is held, and MUST be bounded by a deadline (the smaller of the claim TTL and the caller's remaining budget), not by the compare-and-set attempt count alone. The FIRST guarded write of each loop MAY always run (the single-use token was already consumed upstream and must be persisted best-effort, and a genuine permanent failure must be recorded best-effort), but the loop MUST NOT keep RETRYING — and thus holding the claim — past that deadline: when the deadline passes mid-persist the system MUST stop and surface the transient (non-permanent) contention error (`token_persist_conflict` for the token persist, `status_downgrade_conflict` for the status downgrade), releasing the claim so the caller retries once contention clears, rather than looping until the attempt budget is exhausted. This keeps the TOTAL claim-hold (poll wait + admission + exchange + persist + release) within the caller budget plus a small fixed release, so a contended database write in the persist tail can never keep the claim held long enough for a peer replica to win the claim and re-exchange the already-consumed single-use refresh token.

The transient cross-replica refresh-contention `RefreshError` codes fall into TWO semantically distinct categories that MUST be classified separately, even though their EXTERNAL outcome is identical (all are `transport_error=True`, non-permanent, never cached in the permanent-failure cooldown, never record an account-health penalty, and fail over where applicable): (1) BENIGN CLAIM CONTENTION — `refresh_claim_timeout` — where a peer replica holds the account's refresh claim and THIS caller NEVER exchanged the token (the account's OAuth credentials are entirely healthy; pure contention); and (2) POST-EXCHANGE PERSIST/STATUS CAS CONFLICT — `token_persist_conflict` and `status_downgrade_conflict` — raised AFTER the upstream OAuth exchange when a guarded write lost a compare-and-set. For `token_persist_conflict` the single-use refresh token was already CONSUMED upstream but its rotation could not be persisted, so the database may still hold the just-consumed token; `status_downgrade_conflict` follows a permanent refresh failure whose guarded REAUTH status write lost a compare-and-set. The system MUST expose a narrow predicate recognizing ONLY category (1) (`is_refresh_claim_contention`), a distinct predicate recognizing ONLY category (2) (`is_refresh_persist_conflict`), and a union predicate recognizing BOTH (`is_transient_refresh_contention`). All proxy failover / skip-penalty paths MUST gate their unpenalized-retryable behavior on the UNION predicate (never on the broad `transport_error` flag), so both categories take the same external path; code that specifically means "a peer holds the claim and we did not exchange" MAY use the narrow predicate. A post-exchange persist/status CAS conflict MUST be logged/observed DISTINCTLY from benign claim contention (it signals a rarer, more-serious internal race worth surfacing in logs/metrics). Because a `token_persist_conflict` is not cached and remains non-permanent, a subsequent retry MUST re-run the WHOLE refresh — a fresh upstream re-exchange — rather than reusing the possibly-consumed stored token; the retry MUST NOT treat the transient conflict as an immediate permanent knockout.

When a proxy stream turn NOT hard-pinned to a required account encounters this transient cross-replica refresh-contention failure (`is_transient_refresh_contention` — ONLY the `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` codes, NOT the broad `transport_error` flag), the streaming retry loop MUST exclude the affected account and fail over to a different account rather than reselecting the claimed account until attempts are exhausted, WITHOUT recording an account-health penalty (its credentials are healthy; only its refresh claim is held by a peer replica). This claim-contention failover MUST apply to both the proactive freshness check on the first stream attempt (before any upstream 401) and the forced refresh on the post-401 recovery attempt. Before failing over, the loop MUST release the stream lease it already acquired for the skipped account so that account does not continue to consume one of its stream-concurrency slots for a stream that will never open. On this transient-claim failover the loop MUST also record a retryable `upstream_unavailable` stream error (mirroring the transient aiohttp/connect failover and the WebSocket connect loop): when EVERY candidate account hits a transient refresh-claim timeout before the stream opens and attempts are exhausted, the client MUST receive the temporary `upstream_unavailable` (retryable/capacity) condition rather than a misleading generic `no_accounts` response. A GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`) on either the freshness check or the post-401 forced refresh is NOT claim contention: it MUST NOT take this unpenalized failover path but MUST be handled identically to a connect transport failure — recording the account-health penalty via `_handle_stream_error` and gating failover on the message text — so a persistently broken account is pushed into transient backoff instead of being kept healthy and reselected on the next request. On this movable transport-failure failover the loop MUST also release the skipped account's already-acquired stream lease (setting it to `None`) BEFORE the failover `continue`, symmetrically on BOTH the proactive freshness check and the post-401 forced refresh, matching the claim-contention and permanent-failure branches; otherwise the excluded account keeps holding one of its stream-concurrency slots for the entire duration of the replacement stream.

When a proxy stream turn's proactive freshness check or post-401 forced (`force=True`) refresh raises a PERMANENT `RefreshError` (not a transient claim contention), the streaming retry loop MUST mark the account permanently failed (removing it from selection) AND MUST release the account's already-acquired stream lease BEFORE failing over to the next candidate. Marking the account failed removes it from future selection but does not itself free the stream-concurrency slot the lease occupies; because the failover streams on a different account for the remaining request duration, an unreleased lease would keep the dead account's slot held for that entire duration. This lease release MUST apply symmetrically to BOTH the proactive freshness check on the first stream attempt (before any upstream 401) AND the post-401 forced refresh, matching the transient branches' immediate release at failover.

When a proxy stream turn IS hard-pinned to a required account — a session-continuity `previous_response_id` bound to a preferred account or a file-required preferred account, which sets `preferred_account_id` (and, for `previous_response_id`, `require_preferred_account`) — the movable failover above is correctly skipped so the request never crosses accounts (preserving the account-ownership invariant). But the streaming retry loop MUST NOT then fall through to an unconditional reselect that reselects the same pinned account until attempts are exhausted: on a cross-replica refresh-CLAIM-CONTENTION failure (`is_transient_refresh_contention`) for a hard-pinned stream, the loop MUST release the pinned account's already-acquired stream lease (no leaked slot) and MUST surface a retryable `upstream_unavailable` error promptly rather than spinning pointlessly on the held claim and then surfacing a misleading `no_accounts` result. This hard-pinned handling MUST apply symmetrically to BOTH the proactive freshness check on the first stream attempt (before any upstream 401) AND the forced (`force=True`) refresh on the post-401 recovery attempt, so a hard-pinned stream that opens, receives a 401, and then hits a claim-contention timeout on its forced refresh also stays on the owner, releases the lease, and surfaces the retryable `upstream_unavailable` promptly instead of reselecting the same owner until exhaustion. The transient claim contention MUST NOT be recorded as a permanent failure. A GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`, NOT claim contention) on either the freshness check or the post-401 forced refresh MUST NOT take this unpenalized claim-contention path: it MUST be handled identically to a connect transport failure (recording the account-health penalty via `_handle_stream_error`) so a persistently broken account backs off instead of being kept healthy and reselected. This does not apply to a locally verified cross-transport fresh-replay body, which may still move off the failed owner as specified elsewhere.

The WebSocket connect loop MUST apply the same failover for a transient, cross-replica refresh-CLAIM-CONTENTION failure (`is_transient_refresh_contention` — ONLY the `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` codes, NOT the broad `transport_error` flag) reaching the connect path (on both the proactive freshness check and the post-401 forced refresh): rather than surfacing a bogus 401 `invalid_api_key`, it MUST release the skipped account's already-acquired stream lease, exclude the account, and reselect a healthy account WITHOUT recording an account-health penalty. A GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`, NOT claim contention) on either the freshness check or the post-401 forced refresh MUST NOT take this unpenalized claim-contention path (and MUST NOT surface a terminal 401 `invalid_api_key`): it MUST be treated identically to a connect transport failure — raising a retryable `upstream_unavailable` so the connect loop's normal transport-failure failover/health handling (`_handle_websocket_connect_error`, which records the account-health penalty) applies — so a persistently broken account backs off instead of being kept healthy and reselected. This claim-contention failover MUST be gated only on whether the request is *hard-pinned to a required account* — that is, session-continuity (a `previous_response_id` bound to a preferred account) or a file-required preferred account; it MUST NOT be suppressed merely because a *soft* preferred account is set. In particular, a forced-refresh reconnect auth replay sets the stale account as both the forced-refresh target and the preferred account, but a movable request (no session continuity, no file pin) MUST still exclude the stale account and fail over on a transient transport claim failure. A hard-pinned request MUST stay on its required account (never crossing accounts, never marking a permanent failure), preserving the account-ownership invariant for session-continuity and file-pinned requests; but because the pinned owner's credentials are healthy (its refresh claim is merely held by a peer replica), the connect path MUST NOT surface a terminal 401 `invalid_api_key` for the transient (transport-level / non-permanent) claim failure — it MUST instead release the pinned account's already-acquired stream lease and surface a RETRYABLE `upstream_unavailable` connect failure so the client can retry once the peer replica releases the claim. This hard-pinned handling MUST apply symmetrically to BOTH the proactive freshness check on the connect attempt (before any upstream 401) AND the post-401 forced (`force=True`) refresh recovery attempt. Permanent or non-transport refresh failures keep the terminal 401 `invalid_api_key`. When every account attempt is exhausted by such transient claim failovers, the connect loop MUST emit a proper terminal error to the client (a 503/capacity-style upstream error, not a 401 `invalid_api_key` and not a silent no-op that leaves the client waiting).

The compact-responses path MUST apply the same failover for a transient, cross-replica refresh-CLAIM-CONTENTION failure raised on BOTH its proactive `_ensure_fresh_with_budget` freshness-check preflight AND the post-401 forced (`force=True`) refresh recovery attempt: rather than letting the non-permanent `RefreshError` escape unhandled on the preflight (which surfaces to the client as an unhandled server error) or re-raising the original upstream 401 on the post-401 recovery (which surfaces a misleading `invalid_api_key`), it MUST retain a retryable `upstream_unavailable` error, exclude the account, and reselect a healthy account within the compact account-attempt loop. As on the previsible-unary path, this no-account-health-penalty behavior MUST be gated on the PRECISE claim-contention predicate (`is_transient_refresh_contention`) recognizing ONLY the `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` codes, and MUST NOT be gated on the broad `transport_error` flag alone. The preflight branch MUST additionally release the selected account's `response_create` lease before failover. Because peer-claim contention is not the account's fault (its credentials are healthy; only its refresh claim is held by another replica), this transient-claim failover MUST NOT record an account-health penalty (it MUST NOT call `record_error` / mark the account unhealthy), matching the streaming and WebSocket paths, which only release and exclude the account. Genuine transport-level failures on the compact path — both a `RefreshError` with `code == "transport_error"` (the OAuth refresh request itself timing out / its upstream connection failing) AND raw aiohttp/connect errors, which are NOT refresh-claim contention — MUST retain their existing account-health accounting: they are handled identically to a connect transport failure (gate failover on the message text and `record_error` via `_handle_stream_error` on the skipped account) so a persistently broken account is pushed into transient backoff instead of being kept healthy and reselected on the next request. When the request is pinned to a preferred account, both claim-contention branches MUST instead surface a retriable upstream-unavailable error on that account rather than crossing to another account. On the HTTP bridge / forwarded compact path the caller passes an `api_key_reservation_override` with `owns_reservation` false, making `compact_responses` responsible for finalizing that API-key reservation; therefore EVERY terminal raise in the preflight (proactive-freshness) exception handler MUST settle the compact API-key usage reservation (release it via `_settle_compact_api_key_usage`) BEFORE raising, symmetrically with the post-401 forced-refresh block. This covers not only BOTH pinned transient-claim branches (preflight and post-401 forced refresh), but ALSO the preflight's genuine-transport-error terminal raises (the non-retryable `_raise_proxy_unavailable` and the pinned-transport `_raise_proxy_unavailable`) AND the preflight's permanent-`RefreshError` re-raise, so a file/previous-response-pinned compact whose refresh fails on the preflight — for claim contention, a genuine OAuth transport error, OR a permanent failure — never leaves the reservation unfinished holding API-key quota. (A movable transport-error failover that `continue`s to the next account correctly does NOT settle, because the reservation is carried to the retry.) When EVERY candidate account hits the transient claim timeout and the account-attempt loop is exhausted, the client MUST receive the retained retryable `upstream_unavailable` error rather than the misleading original 401. A permanent or non-transport refresh failure MUST keep its prior escalation (it propagates to the caller) rather than being reinterpreted as a transient failover.

The previsible-unary failover path (`_ensure_previsible_unary_fresh_with_failover`, which serves movable previsible-unary requests such as thread-goal, codex-control, transcription, and file operations) MUST apply the same failover for a transient, cross-replica refresh-CLAIM-CONTENTION failure raised on its proactive `_ensure_fresh_with_budget` freshness check: it MUST exclude the affected account and fail over to a healthy account. The no-account-health-penalty behavior MUST be gated on a PRECISE claim-contention predicate (`is_transient_refresh_contention`) that recognizes ONLY the cross-replica claim/CAS `RefreshError` codes this change introduces — `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` — and MUST NOT be gated on the broad `transport_error` flag alone, because `refresh_access_token` also raises `RefreshError(code="transport_error", transport_error=True)` for a GENUINE OAuth transport failure (the OAuth refresh request itself timing out / its upstream connection failing). Because peer-claim contention is not the account's fault (its credentials are healthy; only its refresh claim is held by another replica), this transient-claim failover MUST NOT record an account-health penalty (it MUST NOT call `record_error` / `_handle_stream_error` for the skipped account), matching the streaming, WebSocket, and compact paths. This transient-claim failover is definitionally transient, so it ALWAYS fails over rather than gating on the message text. Genuine transport-level refresh failures on this path — both a `RefreshError` with `code == "transport_error"` AND raw aiohttp/connect errors, which are NOT refresh-claim contention — MUST retain their existing account-health accounting: they gate failover on the message text and `record_error` (via `_handle_stream_error`) the failed account so a persistently broken account is pushed into transient backoff instead of being reselected on the next request. When the request is strict-pinned to a required account, or when every candidate account is exhausted, a claim-contention failure MUST surface a retryable `upstream_unavailable` error WITHOUT recording a health penalty on the last claim-held account (its caller's terminal error handler MUST recognize the claim-contention-derived `upstream_unavailable` and skip the penalty), so pure cross-replica contention never pushes an otherwise-healthy account into backoff; a genuine transport failure under the same strict-pin/exhaustion condition MUST keep its terminal health penalty.

#### Scenario: Previsible-unary freshness-check claim timeout fails over without a health penalty

- **GIVEN** a movable previsible-unary request (for example a thread-goal request, not pinned to a required account) whose first-selected account is stale and needs a proactive refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the freshness-check preflight raises the transient, transport-level claim error
- **THEN** the previsible-unary failover loop excludes that account and fails over to a healthy account
- **AND** the client receives a normal response served by the healthy account
- **AND** the excluded (claim-held) account is not penalized with a transient account-health error (`record_error` / `_handle_stream_error` is not called) for the peer-claim contention
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Previsible-unary exhausts every account on transient claim failovers without penalty

- **GIVEN** a movable previsible-unary request not pinned to a required account
- **AND** every candidate account's refresh claim is held by another replica so its freshness check raises the transient claim error
- **WHEN** the previsible-unary failover loop excludes each account and exhausts its attempts
- **THEN** the client receives a retryable `upstream_unavailable` error rather than a generic `no_accounts` response
- **AND** no account (including the last one attempted) is penalized with a transient account-health error (`record_error` is not called) for the peer-claim contention
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Previsible-unary genuine transport-error refresh retains its health penalty

- **GIVEN** a movable previsible-unary request (for example a thread-goal request, not pinned to a required account) whose first-selected account is stale and needs a proactive refresh
- **AND** that account's proactive refresh fails with a GENUINE OAuth transport failure — a `RefreshError` with `code == "transport_error"` (the OAuth refresh request itself timed out / its upstream connection failed), NOT cross-replica claim contention
- **WHEN** the freshness-check preflight raises that transport-level `RefreshError`
- **THEN** the previsible-unary failover loop excludes that account and fails over to a healthy account
- **AND** the excluded account IS penalized with a transient account-health error (`record_error` via `_handle_stream_error` is called) so the broken account is pushed into transient backoff instead of being reselected on the next request
- **AND** when the request is strict-pinned to that account (or every candidate is exhausted) the terminal `upstream_unavailable` still records the account-health penalty rather than skipping it as claim contention

#### Scenario: Claim held by another replica past the wait cap

- **GIVEN** an unexpired refresh claim held by another replica
- **WHEN** a refresh waits past the configured wait cap without observing rotated token material
- **THEN** the refresh fails with a transient, non-permanent error
- **AND** the account status is unchanged
- **AND** sticky and bridge sessions are untouched
- **AND** the failure is not cached as a permanent refresh failure

#### Scenario: Winner finishes within the wait cap

- **GIVEN** an unexpired refresh claim held by another replica that completes its token exchange
- **WHEN** the waiting replica observes the rotated refresh-token material within the wait cap
- **THEN** it returns the rotated tokens with zero upstream token exchanges

#### Scenario: Claim wait consumes the caller budget before the exchange

- **GIVEN** a caller refresh-timeout budget and a foreign refresh claim that is held for nearly the whole budget and then releases
- **WHEN** the waiting replica wins the claim after the wait and the material has not rotated
- **THEN** it recomputes the remaining budget before the upstream OAuth exchange
- **AND** it fails fast with the transient (non-permanent) claim-timeout error when no budget remains, rather than starting a full-budget exchange that overruns the request deadline
- **AND** when some budget remains it caps the exchange timeout to that remaining budget

#### Scenario: Admission wait is bounded by the remaining caller budget

- **GIVEN** a caller refresh-timeout budget and a claim winner whose token-refresh admission semaphore is fully saturated (no slot available within the budget)
- **WHEN** the winner tries to acquire token-refresh admission before the upstream OAuth exchange
- **THEN** the admission wait is capped by the remaining budget rather than the full configured admission wait timeout
- **AND** it fails fast with the transient (non-permanent) claim-timeout error and RELEASES the claim within approximately the remaining budget, rather than holding the claim for the full admission timeout and blocking peer replicas

#### Scenario: Claim poll sleep is bounded by the remaining caller budget

- **GIVEN** a caller refresh-timeout budget smaller than the configured poll interval and a live foreign refresh claim that never releases within the budget
- **WHEN** the losing replica polls for the claim to clear
- **THEN** each poll sleep is capped to the smaller of the poll interval and the time remaining before the deadline
- **AND** the loser fails with the transient (non-permanent) claim-timeout error bounded by the caller budget rather than sleeping a full poll interval past the deadline while pinning its repo session and singleflight entry

#### Scenario: Proactive pre-stream claim timeout fails over instead of looping

- **GIVEN** a proxy stream turn whose first-selected account is stale and needs a proactive refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the first-attempt freshness check raises the transient claim error before any upstream 401
- **THEN** the streaming retry loop excludes that account and fails over to a healthy account
- **AND** the excluded account's already-acquired stream lease is released before failover
- **AND** the request does not exhaust attempts as `no_accounts` while a healthy alternate exists

#### Scenario: Stream retry exhausts every account on transient claim failovers

- **GIVEN** a proxy stream turn not pinned to a preferred/required account
- **AND** every candidate account's refresh claim is held by another replica so its proactive freshness check raises the transient claim error before the stream opens
- **WHEN** the streaming retry loop excludes each account and exhausts its attempts
- **THEN** the client receives a retryable `upstream_unavailable` error rather than a generic `no_accounts` response
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Stream retry exhausts every account on post-401 forced-refresh claim failovers

- **GIVEN** a proxy stream turn not pinned to a preferred/required account
- **AND** every candidate account opens far enough to receive an upstream 401, and its subsequent forced (`force=True`) refresh raises the transient claim error because the claim is held by another replica
- **WHEN** the streaming retry loop releases each account's stream lease, excludes it, and exhausts its attempts
- **THEN** the client receives a retryable `upstream_unavailable` error rather than a generic `no_accounts` response
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Permanent proactive-refresh failure releases the stream lease before failover

- **GIVEN** a movable proxy stream turn whose first-selected account's proactive freshness check raises a PERMANENT `RefreshError`
- **WHEN** the streaming retry loop marks the account permanently failed and fails over
- **THEN** the account's already-acquired stream lease is released BEFORE the failover streams on the replacement account
- **AND** the failed account never serves the stream while a healthy alternate does

#### Scenario: Permanent post-401 forced-refresh failure releases the stream lease before failover

- **GIVEN** a movable proxy stream turn that opens on its account, receives an upstream 401, and whose forced (`force=True`) refresh then raises a PERMANENT `RefreshError`
- **WHEN** the streaming retry loop marks the account permanently failed and fails over
- **THEN** the account's already-acquired stream lease is released BEFORE the failover streams on the replacement account
- **AND** the failed account never serves the replacement stream while a healthy alternate does

#### Scenario: Hard-pinned stream turn stays on its owner account on transient claim timeout

- **GIVEN** a hard-pinned proxy stream turn (a session-continuity `previous_response_id` bound to a preferred account, which sets `preferred_account_id` and `require_preferred_account`)
- **AND** the pinned owner account's refresh claim is held by another replica so its proactive freshness check raises the transient, transport-level claim error before the stream opens
- **WHEN** the streaming retry loop evaluates the transient claim failure for the pinned request
- **THEN** the loop does NOT cross to another account (the account-ownership invariant is preserved)
- **AND** the pinned account's already-acquired stream lease is released (not leaked)
- **AND** the client receives a retryable `upstream_unavailable` error promptly rather than pointless retries that exhaust into a misleading `no_accounts` response
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Hard-pinned stream turn stays on its owner account on post-401 forced-refresh claim timeout

- **GIVEN** a hard-pinned proxy stream turn (a session-continuity `previous_response_id` bound to a preferred account, which sets `preferred_account_id` and `require_preferred_account`)
- **AND** the pinned owner account's proactive freshness check succeeds so the stream opens, but the upstream returns a 401 and the subsequent forced (`force=True`) refresh raises the transient, transport-level claim error because the claim is held by another replica
- **WHEN** the streaming retry loop evaluates the transient claim failure for the pinned request on the post-401 recovery attempt
- **THEN** the loop does NOT cross to another account (the account-ownership invariant is preserved)
- **AND** the pinned account's already-acquired stream lease is released (not leaked)
- **AND** the client receives a retryable `upstream_unavailable` error promptly rather than reselecting the same owner until attempts exhaust into a misleading `no_accounts` response
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Stream genuine transport-error refresh retains its health penalty

- **GIVEN** a movable proxy stream turn (not hard-pinned) whose selected account needs a refresh
- **AND** that account's refresh fails with a GENUINE OAuth transport failure — a `RefreshError` with `code == "transport_error"` (the OAuth refresh request itself timed out / its upstream connection failed), NOT cross-replica claim contention
- **WHEN** the streaming retry loop evaluates the failure on either the proactive freshness check or the post-401 forced refresh
- **THEN** the loop records the account-health penalty (`record_error` via `_handle_stream_error`) on the skipped account (unlike a claim-contention timeout, which is not penalized) and fails over to a healthy account
- **AND** the failed account is pushed into transient backoff instead of being kept healthy and reselected on the next request
- **AND** the failed account's already-acquired stream lease is released BEFORE the replacement account streams, so its stream-concurrency slot is not held for the duration of the replacement stream

#### Scenario: WebSocket connect claim timeout fails over instead of 401

- **GIVEN** a WebSocket responses connection whose first-selected account needs a refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the connect path raises the transient, transport-level claim error
- **THEN** the connect loop excludes that account and fails over to a healthy account
- **AND** the excluded account's already-acquired stream lease is released before failover
- **AND** the client receives the upstream response rather than a 401 `invalid_api_key`

#### Scenario: Movable forced-refresh reconnect fails over on transient claim timeout

- **GIVEN** a movable WebSocket responses request (no session-continuity `previous_response_id`, no file-required preferred account)
- **AND** a reconnect auth replay has set the stale account as both the forced-refresh target and the (soft) preferred account
- **WHEN** the forced refresh on that account raises the transient, transport-level claim error
- **THEN** the connect loop excludes the stale account and fails over to a healthy account
- **AND** the stale account's already-acquired stream lease is released before failover
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Hard-pinned WebSocket connect stays on its owner and returns retryable error on freshness-check claim timeout

- **GIVEN** a hard-pinned WebSocket responses request (session-continuity `previous_response_id` bound to a preferred account, which sets `preferred_account_id` and `require_preferred_account`)
- **AND** the pinned owner account's refresh claim is held by another replica so its proactive connect-path freshness check raises the transient, transport-level claim error before the upstream websocket opens
- **WHEN** the connect path evaluates the transient claim failure for the pinned request
- **THEN** the connect loop does NOT cross to another account (the account-ownership invariant is preserved)
- **AND** the pinned account's already-acquired stream lease is released (not leaked)
- **AND** the client receives a RETRYABLE `upstream_unavailable` connect failure rather than a terminal 401 `invalid_api_key`
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Hard-pinned reconnect stays on its required account and returns retryable error on post-401 forced-refresh claim timeout

- **GIVEN** a hard-pinned WebSocket responses request (session-continuity `previous_response_id` bound to a preferred account, or a file-required preferred account)
- **AND** a reconnect auth replay has set that required account as the forced-refresh target
- **WHEN** the post-401 forced refresh on that account raises the transient, transport-level claim error
- **THEN** the connect loop does NOT cross to another account
- **AND** the pinned account's already-acquired stream lease is released (not leaked)
- **AND** the client receives a RETRYABLE `upstream_unavailable` connect failure rather than a terminal 401 `invalid_api_key`
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: WebSocket connect exhausts every account on transient claim failovers

- **GIVEN** a WebSocket responses connection not pinned to a preferred/required account
- **AND** every account attempt (up to the WebSocket max-account-attempts) raises the transient, transport-level claim error
- **WHEN** the connect loop excludes each account and exhausts its attempts
- **THEN** the client receives a proper terminal error frame (a 503/capacity-style upstream error), not a 401 `invalid_api_key` and not a silent no-op
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: WebSocket genuine transport-error refresh retains its health penalty

- **GIVEN** a WebSocket responses connection whose selected account needs a refresh
- **AND** that account's refresh fails with a GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`), NOT cross-replica claim contention
- **WHEN** the connect path evaluates the failure on either the proactive freshness check or the post-401 forced refresh
- **THEN** the connect path raises a retryable `upstream_unavailable` routed through the connect-error penalty/failover path (`_handle_websocket_connect_error`, which records the account-health penalty) rather than the unpenalized `_WebSocketTransientRefreshFailover` claim-contention path or a terminal 401 `invalid_api_key`
- **AND** the failed account is penalized and the request fails over to a healthy account
- **AND** the genuine transport failure is never recorded as a permanent failure

#### Scenario: Compact freshness-check claim timeout fails over instead of erroring out

- **GIVEN** a compact-responses request whose first-selected account is stale and needs a proactive refresh
- **AND** that account's refresh claim is held by another replica past the wait cap
- **WHEN** the freshness-check preflight raises the transient, transport-level claim error
- **THEN** the compact account-attempt loop releases the account's `response_create` lease, excludes that account, and fails over to a healthy account
- **AND** the client receives a normal compact response rather than an unhandled server error
- **AND** the transient claim contention is never recorded as a permanent failure
- **AND** the excluded account is not penalized with a transient account-health error (`record_error` is not called) for the peer-claim contention

#### Scenario: Compact post-401 forced-refresh claim timeout fails over instead of surfacing 401

- **GIVEN** a compact-responses request not pinned to a preferred account whose selected account returns an upstream 401
- **AND** the post-401 forced (`force=True`) refresh raises the transient, transport-level claim error because the claim is held by another replica
- **WHEN** the compact account-attempt loop retains a retryable `upstream_unavailable`, excludes that account, and fails over to a healthy account
- **THEN** the client receives a normal compact response rather than the misleading original 401
- **AND** the excluded account is not penalized with a transient account-health error (`record_error` is not called) for the peer-claim contention
- **AND** when every candidate account hits the transient claim timeout and attempts are exhausted, the client receives the retryable `upstream_unavailable` error rather than the 401
- **AND** the transient claim contention is never recorded as a permanent failure

#### Scenario: Compact genuine transport-error refresh retains its health penalty

- **GIVEN** a compact-responses request (not pinned) whose selected account needs a refresh
- **AND** that account's refresh fails with a GENUINE OAuth transport failure (a `RefreshError` with `code == "transport_error"`), NOT cross-replica claim contention, on either the freshness-check preflight or the post-401 forced refresh
- **WHEN** the compact account-attempt loop evaluates the failure
- **THEN** it records the account-health penalty (`record_error` via `_handle_stream_error`) on the skipped account (unlike a claim-contention timeout, which is not penalized) and fails over to a healthy account
- **AND** when every candidate account hits the genuine transport failure and attempts are exhausted, the client receives a retryable `upstream_unavailable` error

#### Scenario: Pinned compact refresh-claim timeout settles the API-key reservation before raising

- **GIVEN** a file/previous-response-pinned compact-responses request invoked through the HTTP bridge / forwarded path with an `api_key_reservation_override` and `owns_reservation` false
- **AND** the pinned owner account's refresh claim is held by another replica so its freshness-check preflight (or post-401 forced refresh) raises the transient, transport-level claim error
- **WHEN** the compact account-attempt loop surfaces the retryable `upstream_unavailable` for the pinned request (which cannot cross accounts)
- **THEN** the compact API-key usage reservation is settled (released via `_settle_compact_api_key_usage`) before the error is raised, so it does not leak held API-key quota
- **AND** the client receives a retryable `upstream_unavailable` error rather than an unhandled server error

### Requirement: Refresh-path sibling writes never clobber a peer rotation, and the warmup path honors the claim-contention taxonomy

The no-unconditional-write and no-clobber guarantees MUST hold across the WHOLE write surface reachable on the refresh/`ensure_fresh` hot path, not only inside the three rewritten helpers. This invariant MUST be enforced STRUCTURALLY at the repository (data) layer so no current or future caller can reopen the clobber class:

Refresh-token ciphertext writes MUST be compare-and-set at the repository layer. The accounts repository MUST expose exactly ONE method that writes access/refresh/id token ciphertext (`rotate_tokens`), and that method MUST take a REQUIRED (non-optional) `expected_refresh_token_encrypted` compare-and-set predicate — there MUST be no parameter combination that writes `refresh_token_encrypted` unconditionally. Metadata writes MUST NOT touch token material: the repository MUST expose a separate metadata-only method (`update_account_metadata`) for identity/plan/workspace fields (`chatgpt_account_id`, `chatgpt_user_id`, `plan_type`, `email`, `workspace_id`, `workspace_label`, `seat_type`, `last_refresh`) that STRUCTURALLY cannot write token ciphertext (it has no parameter for it). Consequently a metadata-only writer holding a stale `Account` snapshot — loaded before a peer replica's guarded refresh — can never clobber a concurrent rotation, because the only code path that touches `refresh_token_encrypted` is the mandatory compare-and-set in `rotate_tokens`. Every caller MUST route to the correct method: token-rotation callers (the refresh persist and permanent-failure paths) to the guarded `rotate_tokens`, and metadata-only callers (the `chatgpt_account_id` backfill and the usage identity/plan/workspace sync) to `update_account_metadata`.

The `chatgpt_account_id` backfill (`_ensure_chatgpt_account_id`), which runs on every `ensure_fresh` — including the fast no-refresh path — for a legacy account still missing its `chatgpt_account_id`, MUST persist the derived `chatgpt_account_id` through the metadata-only writer, which structurally cannot touch token material. Its caller-time in-memory selection snapshot is not re-read under a claim, so routing the backfill through a token-writing method would risk clobbering a concurrent peer rotation of the single-use token that lands in the read→write window with already-consumed material; the metadata-only path removes that risk entirely (a concurrent rotation is simply not observable to this write, and the derived id is persisted without ever reading or writing the refresh-token ciphertext). No `ensure_fresh` path may perform an unconditional token write, and no metadata-only path may write token ciphertext at all.

The post-exchange token persist MUST NOT drop a freshly rotated token on a compare-and-set miss. After the upstream OAuth exchange has succeeded (the old single-use refresh token consumed, a new one minted), a compare-and-set miss — whether the bounded same-plaintext re-encryption retries are exhausted OR the claim/caller deadline cuts the retry loop — MUST NOT raise a transient `token_persist_conflict` in place of attempting to persist the new token. On any such miss the newly-rotated token MUST get a DEDICATED, small, bounded final-persist retry loop (a few guarded compare-and-set attempts with tiny backoff) keyed on the LAST-OBSERVED ciphertext, DELIBERATELY SEPARATE from the claim/caller deadline (persisting a valid single-use rotated token is worth a few extra milliseconds over budget); because each attempt is itself a compare-and-set it lands only when nothing changed since the read (no clobber) and misses harmlessly otherwise. On each miss the system MUST re-read once more and decide on the decrypted plaintext: a genuinely different stored plaintext means a peer rotation legitimately superseded ours and MUST be ADOPTED (never overwritten); the same plaintext merely re-encrypted MUST be RETRIED against the newly observed ciphertext within the dedicated budget; undecryptable material stops the dedicated retries. Because any ciphertext change means a writer committed and no realistic writer re-encrypts the same consumed token in a tight loop, the dedicated retries land the rotation within a couple of attempts in every realistic case. Only if the dedicated final retries are ALL exhausted while the stored material stays the already-consumed token does the system reach a SAFE TERMINAL OUTCOME: it MUST NOT drop the rotation behind a bare transient `token_persist_conflict` that a later blind retry would turn into an `invalid_grant`/reauth PERMANENT knockout of a healthy account, and MUST instead FAIL CLOSED by flagging the account `REAUTH_REQUIRED` through the SAME ciphertext-guarded status compare-and-set (adopting a genuine peer rotation observed in the guard window, never clobbering it; demoting the transient conflict to a last resort only if even the guarded status write cannot land). The deadline therefore bounds the RETRY LOOP and the network/admission waits, never the dedicated final-persist retries or the safe terminal flag — a crashed-storm retry budget can never be the reason a freshly minted token is dropped while the database keeps a consumed one, nor the reason a healthy account is permanently knocked out.

The permanent-status downgrade MUST have a SINGLE guarded authority. `AuthManager` (`_handle_permanent_refresh_failure`) owns the primary refresh-token-ciphertext-guarded compare-and-set. The proxy load balancer's `mark_permanent_failure` MUST NOT perform an UNGUARDED database status write on the permanent-refresh-failure path: its persistence MUST route through a compare-and-set conditioned on the account's refresh-token ciphertext (`update_status_if_current` with `expected_refresh_token_encrypted`), so a concurrent peer re-authentication/import rotation causes a MISS instead of clobbering the peer's repaired `ACTIVE`/rotated row back to `reauth_required` (and tearing down its sticky/bridge sessions). A genuine permanent failure MUST still result in exactly ONE guarded database downgrade: in the single-caller case `AuthManager` has already CAS-written the downgrade and mutated the in-memory object, so the load balancer's guarded write is predicate-skipped as redundant; the load balancer's guarded write covers only the callers whose in-memory object did not go through that CAS (an intra-process singleflight joiner sharing the winner's permanent error) and non-refresh permanent failures. No path may perform an unguarded status downgrade write on the refresh-permanent-failure path. The local routing overlay MUST honor the guarded-CAS result: `mark_permanent_failure` MUST mark the account routing-unavailable in this replica's local overlay ONLY when the guarded downgrade actually applied (the compare-and-set landed, or no write was needed because the primary authority already CAS-wrote it). When the compare-and-set MISSES because a peer replica repaired/rotated the row (the database row is left `ACTIVE`), the caller MUST NOT mark the account routing-unavailable — excluding a freshly repaired healthy account from local routing would be a self-inflicted routing loss that undermines the CAS guard — so the account remains selectable in this replica.

The proxy warmup submit path MUST classify a refresh failure with the same taxonomy as the core proxy request paths: a transient cross-replica refresh-CLAIM-CONTENTION failure (`is_transient_refresh_contention` — the `refresh_claim_timeout`, `status_downgrade_conflict`, and `token_persist_conflict` codes) MUST surface as a retryable `upstream_unavailable` in the warmup result and request log, NOT as `invalid_api_key`, because the account's OAuth credentials are healthy (only its refresh claim is held by a peer replica). A permanent `RefreshError` keeps its `invalid_api_key` classification (and marks the permanent failure), and a genuine non-contention transport-level `RefreshError` also keeps `invalid_api_key`.

#### Scenario: Legacy chatgpt_account_id backfill routes through the metadata-only writer

- **GIVEN** a legacy account whose `chatgpt_account_id` is unset but whose stored id-token yields a derivable `chatgpt_account_id`
- **AND** the account's token material is fresh, so `ensure_fresh` takes the no-refresh fast path straight into the backfill
- **WHEN** `ensure_fresh` runs and persists the derived `chatgpt_account_id`
- **THEN** the write goes through the metadata-only repository method, which has no parameter for token ciphertext and therefore never reads or writes `refresh_token_encrypted`
- **AND** a concurrent peer rotation of the single-use refresh token is untouched, because the backfill is structurally incapable of writing token material

#### Scenario: Repository refuses an unguarded refresh-token write

- **GIVEN** the accounts repository's token-writing method (`rotate_tokens`)
- **WHEN** any caller attempts to persist token ciphertext
- **THEN** the method requires a non-optional `expected_refresh_token_encrypted` compare-and-set predicate, so there is no code path that writes `refresh_token_encrypted` unconditionally
- **AND** a concurrent rotation committed after the caller read the expected ciphertext turns a stale writer into a guarded MISS (no write, no clobber), never an unconditional overwrite

#### Scenario: Metadata write cannot touch token material

- **GIVEN** the accounts repository's metadata-only method (`update_account_metadata`)
- **WHEN** an identity/plan/workspace sync writes account metadata from a stale in-memory snapshot
- **THEN** the method has no parameter for access/refresh/id token ciphertext and persists only metadata columns
- **AND** the stored token material is left exactly as it was, so a concurrent refresh-token rotation is never clobbered by a metadata write

#### Scenario: Proxy permanent-failure mark does not clobber a peer's rotated repair

- **GIVEN** a proxy caller holds a stale in-memory account object (still `ACTIVE`, holding the OLD refresh-token ciphertext that just failed permanently) — for example an intra-process singleflight joiner that received the winner's re-raised permanent `RefreshError`
- **AND** a peer replica has already re-authenticated/rotated that account in the database to `ACTIVE` with a freshly rotated refresh token
- **WHEN** the proxy calls `mark_permanent_failure` for the account
- **THEN** the guarded status compare-and-set (conditioned on the old refresh-token ciphertext) MISSES the rotated ciphertext and performs no write
- **AND** the peer's repaired `ACTIVE`/rotated row is NOT clobbered back to `reauth_required` and its sessions are not torn down
- **AND** the caller MUST NOT mark the account routing-unavailable in this replica's local overlay, so the freshly repaired `ACTIVE` account remains selectable here

#### Scenario: Proxy permanent-failure mark still downgrades when no peer rotation occurred

- **GIVEN** a genuine permanent refresh failure with no concurrent peer rotation (the in-memory refresh-token ciphertext matches the stored row)
- **WHEN** the proxy calls `mark_permanent_failure` for the account
- **THEN** the single guarded status compare-and-set lands and the account is downgraded to `reauth_required`
- **AND** the account IS marked routing-unavailable in this replica's local overlay (excluded from local selection), because the permanent downgrade actually applied

#### Scenario: Post-exchange persist runs dedicated final retries when the deadline cuts the retry loop

- **GIVEN** a claim winner completed the upstream exchange (new refresh token minted, old one consumed) and enters the token-persist compare-and-set loop while holding the claim
- **AND** the guarded compare-and-set keeps missing on a sustained same-plaintext re-encryption storm while the claim/caller deadline has already passed
- **WHEN** the deadline cuts the retry loop
- **THEN** the loop stops retrying but STILL runs the DEDICATED, bounded final-persist retries keyed on the last-observed ciphertext (which are separate from the deadline), NOT the full attempt budget, and NEVER an unconditional write
- **AND** only if those dedicated final retries are ALL exhausted while the stored material stays the already-consumed token does the persist reach the SAFE TERMINAL OUTCOME, flagging the account `REAUTH_REQUIRED` through the guarded status compare-and-set rather than dropping the rotation behind a bare transient `token_persist_conflict` that a later blind retry would turn into a permanent knockout
- **AND** the claim is released so the total claim-hold stays within the caller budget plus the small fixed dedicated-retry headroom and release

#### Scenario: Deadline-cut persist lands the rotated token when the stored plaintext is unchanged

- **GIVEN** a claim winner completed the upstream exchange and the claim/caller deadline has already elapsed
- **AND** the stored refresh-token plaintext is still exactly the consumed token (only re-encrypted), so the first guarded write missed on the shifted ciphertext
- **WHEN** the deadline cuts the retry loop and the final ciphertext-guarded persist runs against the last-observed ciphertext
- **THEN** the final guarded write lands the freshly rotated token, the database no longer holds the consumed token, and NO transient conflict is raised

#### Scenario: Deadline-cut persist adopts a genuine peer rotation on the final re-read

- **GIVEN** a claim winner completed the upstream exchange and the claim/caller deadline has already elapsed
- **AND** a genuinely different peer rotation lands right before the final guarded persist
- **WHEN** the final ciphertext-guarded persist misses the peer's ciphertext and the persist re-reads the row
- **THEN** the stored plaintext is genuinely different, so the peer rotation is ADOPTED (the winner's freshly rotated token is legitimately superseded) rather than overwritten, and no unconditional write is ever issued

#### Scenario: Warmup refresh-claim contention surfaces upstream_unavailable, not invalid_api_key

- **GIVEN** two replicas warm the same account concurrently and a peer replica holds the account's refresh claim
- **WHEN** the warmup submit path's `_ensure_fresh_with_budget` raises a transient `refresh_claim_timeout` (`is_refresh_claim_contention`)
- **THEN** the warmup result and request log record a retryable `upstream_unavailable` error code
- **AND** the healthy account is NOT reported as an `invalid_api_key` authentication failure

#### Scenario: Pinned compact preflight transport-error / permanent failure settles the reservation before raising

- **GIVEN** a file/previous-response-pinned compact-responses request over the HTTP bridge / forwarded path with an `api_key_reservation_override` and `owns_reservation` false
- **AND** its freshness-check preflight fails with either a genuine OAuth `transport_error` `RefreshError` or a permanent `RefreshError`
- **WHEN** the preflight exception handler reaches its terminal raise
- **THEN** the compact API-key usage reservation is settled (released via `_settle_compact_api_key_usage`) before the error is raised, so it does not leak held API-key quota

### Requirement: Warm-up attempt dedup is atomic across processes

Limit warm-up attempt deduplication SHALL be enforced by the database as a
single atomic statement so that it holds across replicas and across processes
sharing one SQLite file: the tolerance-window duplicate check and the attempt
insert MUST NOT be separable into a check-then-act sequence observable by
concurrent writers, and on PostgreSQL concurrent attempt inserts for the same
account and window MUST be serialized. Two warm-up attempts whose `reset_at`
values differ by no more than the configured tolerance MUST NOT both persist
for the same account and window. Per-process locks MAY additionally throttle
local writes but MUST NOT be the mechanism that guarantees dedup.

#### Scenario: Two processes observe near-duplicate reset candidates

- **GIVEN** two refresh workers in separate processes share one database
- **AND** both observe the same account and window with `reset_at` values that
  differ by less than the configured tolerance
- **WHEN** both record a warm-up attempt concurrently
- **THEN** at most one attempt row persists for that account/window tolerance
  window
- **AND** the losing worker receives no attempt and sends no warm-up probe

#### Scenario: Exact-tuple duplicates remain constrained

- **GIVEN** a warm-up attempt already persists for an account, window, and
  `reset_at` tuple
- **WHEN** another worker inserts the identical tuple despite the atomic guard
- **THEN** the unique constraint rejects the duplicate
- **AND** the worker treats the rejection as a dedup skip rather than an error

### Requirement: Compact budget-exhausted terminals settle the API-key reservation before raising

On the HTTP bridge / forwarded compact path the caller passes an `api_key_reservation_override` with `owns_reservation` false, making `compact_responses` the SOLE settler of the API-key usage reservation; therefore EVERY budget-exhausted terminal raise in the compact request path that is reached with a held, unsettled reservation MUST settle the compact API-key usage reservation (release it via `_settle_compact_api_key_usage` with `response` `None`) BEFORE raising the budget-exhausted `ProxyResponseError` (`upstream_request_timeout`), so held API-key quota is not leaked. This MUST apply to the outer-loop preflight budget terminals (before the freshness check, before the freshness reserve, and after the freshness check) and to the post-401 forced-refresh preflight budget terminal, each of which propagates straight to the outer `except ProxyResponseError` handler (which does not settle) and the `finally` (which only writes a request log). The terminal MUST preserve its prior escalation: it still raises the same `502` `upstream_request_timeout` error after settling, and it MUST still release the selected account's `response_create` lease where it already did so. A budget-exhausted terminal that is caught by an enclosing handler that already settles the reservation before raising — the inner upstream-call budget terminals, whose `upstream_request_timeout` error is settled by the retry loop's `upstream_request_timeout` / account-neutral branch — MUST NOT settle a second time, so the reservation is never double-settled.

#### Scenario: Compact preflight budget exhaustion settles the reservation before raising

- **GIVEN** a compact-responses request invoked through the HTTP bridge / forwarded path with an `api_key_reservation_override` and `owns_reservation` false
- **WHEN** a budget-exhausted terminal fires on the compact preflight (the request budget is already exhausted at a freshness-check preflight or post-401 forced-refresh preflight budget check)
- **THEN** the compact API-key usage reservation is settled (released via `_settle_compact_api_key_usage`) before the budget-exhausted error is raised, so it does not leak held API-key quota
- **AND** the client receives the `502` `upstream_request_timeout` error unchanged

#### Scenario: Inner upstream-call budget terminal is not double-settled

- **GIVEN** a compact-responses request whose inner `_call_compact` budget check finds the request budget exhausted and raises the budget-exhausted `upstream_request_timeout` error
- **WHEN** the enclosing retry-loop `except ProxyResponseError` handler settles the reservation on the `upstream_request_timeout` branch before raising
- **THEN** no additional settle is performed at the inner terminal, so the reservation is settled exactly once

