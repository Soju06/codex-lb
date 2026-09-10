## MODIFIED Requirements

### Requirement: Background usage refresh reconciles recoverable blocked statuses

Background usage refresh SHALL reconcile persisted `rate_limited` and `quota_exceeded` accounts back to `active` after it writes fresh usage snapshots that prove the blocked window has recovered. This reconciliation SHALL be recovery-only and SHALL NOT promote `active` accounts into blocked statuses. For `rate_limited` accounts, recovery evidence SHALL come from the most recently recorded main-window row: when a post-block refresh no longer reports a short primary window and the last primary sample's own reset deadline has elapsed (or no primary sample exists), a fresh long-window row recorded after the block that still reports usage below `100%` proves recovery. While the last primary sample still claims an unexpired window (or omits reset metadata), or the newer long-window row is itself exhausted, primary freshness SHALL keep gating recovery.

A future persisted `reset_at` SHALL continue to block ordinary recovery except for a `rate_limited` Free account whose monthly usage history proves that the specific monthly window associated with the current block reset. This exception MUST require `blocked_at` and a future persisted `reset_at`, at least 30 seconds elapsed after `blocked_at`, a monthly baseline recorded strictly after `blocked_at` whose `reset_at` matches the persisted marker within five seconds, a real temporal reset in an adjacent monthly pair at or after that baseline, and both the transition's after sample and the latest monthly sample recorded after `blocked_at` with usage below `100%`. The reset pair MAY come from the current refresh or be selected from adjacent persisted post-block samples so recovery survives a process restart and tolerates sliding reset deadlines without comparing non-neighboring rows. Availability without that matching anchored transition, reset timestamp jitter, an exhausted latest window, or evidence for a non-Free account MUST NOT override the persisted cooldown.

Every recovery write MUST compare the current status, deactivation reason, `reset_at`, and `blocked_at`. A successful write SHALL set the account to `active` and clear the deactivation reason and both block markers. A compare-and-set miss MUST preserve the newer row and MUST NOT make the stale account snapshot eligible for warm-up.

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
- **AND** no qualifying reset-confirmed Free monthly transition matches the current block
- **THEN** the account stays `rate_limited` until fresh primary evidence arrives, the primary sample's reset deadline elapses, or a qualifying monthly reset is confirmed

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
- **AND** a later background usage refresh writes fresh available usage
- **AND** no qualifying reset-confirmed Free monthly transition matches the current block
- **THEN** the scheduler leaves the account `rate_limited`

#### Scenario: Confirmed Free monthly reset recovers before a stale deadline
- **GIVEN** a Free account is persisted as `rate_limited` with `blocked_at` more than 30 seconds ago and a future `reset_at`
- **AND** a monthly baseline recorded strictly after `blocked_at` has a reset deadline within five seconds of the persisted marker
- **WHEN** background usage refresh confirms a real transition in an adjacent monthly pair at or after that matching baseline
- **AND** the transition's after sample and latest monthly sample were recorded after `blocked_at` and report usage below `100%`
- **THEN** the scheduler atomically marks the account `active` before the stale persisted deadline
- **AND** it clears `reset_at`, `blocked_at`, and the deactivation reason

#### Scenario: Persisted monthly transition recovers after scheduler restart
- **GIVEN** a qualifying Free monthly reset transition was persisted after `blocked_at`
- **AND** the scheduler process restarts after the transition is no longer the current in-memory before/after pair
- **WHEN** the restarted scheduler refreshes the still-`rate_limited` account before its stale persisted deadline
- **THEN** it may use a matching persisted baseline plus a later adjacent monthly transition pair as reset evidence
- **AND** it recovers the account through the same marker-guarded transition

#### Scenario: A baseline at the exact block timestamp cannot shadow a later valid baseline
- **GIVEN** persisted monthly history contains a reset-matching row recorded exactly at `blocked_at`
- **AND** a later row recorded strictly after `blocked_at` matches the same persisted reset marker
- **AND** an adjacent reset transition follows that later row
- **WHEN** the restarted scheduler resolves persisted recovery evidence
- **THEN** it MUST ignore the row recorded exactly at `blocked_at`
- **AND** it MUST use the later matching baseline to evaluate the qualifying transition

#### Scenario: An unanchored current transition cannot mask persisted recovery evidence
- **GIVEN** the current monthly before/after pair confirms a reset whose baseline does not match the blocked Free account's persisted reset marker
- **AND** persisted monthly history contains an eligible post-block baseline plus a qualifying adjacent reset transition
- **WHEN** the scheduler resolves monthly reset evidence
- **THEN** it MUST scan persisted history instead of short-circuiting on the unanchored current pair
- **AND** it MUST use evidence anchored to the persisted block marker for recovery and warm-up

#### Scenario: Minimum post-block floor prevents immediate recovery
- **GIVEN** a Free account was marked `rate_limited` less than 30 seconds ago
- **AND** monthly samples otherwise appear to prove a reset with available quota
- **WHEN** background usage refresh evaluates recovery
- **THEN** the account remains `rate_limited` with both block markers intact

#### Scenario: Mismatched monthly baseline does not recover the current block
- **GIVEN** a Free account has a future persisted rate-limit deadline
- **AND** monthly history contains a real reset transition whose baseline deadline differs from that marker by more than five seconds
- **WHEN** background usage refresh evaluates recovery
- **THEN** the transition is not treated as evidence for the current block
- **AND** the account remains `rate_limited`

#### Scenario: Later exhausted monthly state defeats older recovery evidence
- **GIVEN** a Free account has a qualifying post-block monthly reset transition whose after sample reports available quota
- **AND** its latest monthly sample reports usage at or above `100%`
- **WHEN** background usage refresh evaluates recovery
- **THEN** the account remains blocked

#### Scenario: Plus primary exhaustion is not released by monthly evidence
- **GIVEN** a Plus account is persisted as `rate_limited`
- **AND** its current primary usage reports `100%`
- **WHEN** background usage refresh observes available long-window usage or an unrelated reset transition
- **THEN** the account remains `rate_limited`

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
- **AND** no newer long-window row or qualifying post-block monthly reset transition proves recovery
- **THEN** the scheduler leaves the account blocked

#### Scenario: Scheduler skips recovery when the account row changed concurrently
- **WHEN** background usage refresh determines that a blocked account is recoverable
- **AND** the persisted account status, reason, or reset markers change before the scheduler writes recovery
- **THEN** the scheduler skips the stale recovery write
- **AND** warm-up does not use that stale recovery decision

#### Scenario: Scheduler clears stale deactivation reasons on recovery
- **WHEN** background usage refresh recovers a `rate_limited` or `quota_exceeded` account to `active`
- **THEN** the scheduler writes `deactivation_reason` as `NULL`

### Requirement: Reset-confirmed limit warm-up

The system SHALL support an optional limit warm-up mechanism that is disabled by default. When enabled globally and for an account, background usage refresh MAY send one minimal upstream Responses request after it confirms that a selected quota window moved into a newly available reset window. Eligibility SHALL depend on a real reset transition and the configured post-reset availability gate, not on whether the previous window was exhausted. The legacy `limit_warmup_exhausted_threshold_percent` setting MUST NOT gate reset-confirmed eligibility.

Background usage refresh MUST complete any applicable blocked-status reconciliation before warm-up evaluation. Candidate evaluation and the sender's fresh preflight check MUST both require the account to be `active`; paused, deactivated, `reauth_required`, `rate_limited`, and `quota_exceeded` accounts MUST NOT receive warm-up traffic. When a reset-confirmed recovery uses persisted transition evidence, warm-up SHALL reuse that same before/after pair so the new account/window/reset tuple enters the ordinary durable deduplication path.

The configured `limit_warmup_cooldown_seconds` SHALL gate only staggered idle warm-up candidates. It MUST NOT suppress a reset-confirmed candidate for a distinct account/window/reset tuple, which remains protected by the durable atomic attempt claim for that tuple.

#### Scenario: Warm-up follows a real reset regardless of prior usage
- **GIVEN** limit warm-up is enabled globally and for an active account
- **AND** the account's previous usage sample for a selected window reports any usage below or at exhaustion
- **WHEN** background usage refresh records a newer sample that proves a real reset for that window and satisfies the configured availability gate
- **THEN** the system sends at most one warm-up request for that account/window/reset tuple

#### Scenario: Staggered idle cooldown does not suppress a distinct reset tuple
- **GIVEN** an account has a recent warm-up attempt inside `limit_warmup_cooldown_seconds`
- **AND** background usage refresh confirms a different selected account/window/reset tuple
- **WHEN** reset-confirmed warm-up evaluates the new tuple
- **THEN** the staggered idle cooldown MUST NOT suppress that candidate
- **AND** the durable attempt claim MUST still prevent another send for an already claimed identical tuple

#### Scenario: Warm-up is skipped unless reset is confirmed
- **GIVEN** limit warm-up is enabled globally and for an account
- **WHEN** background usage refresh records a newer available sample without a real selected-window reset transition
- **THEN** the system MUST NOT send a reset-confirmed warm-up request for that sample

#### Scenario: Warm-up is not triggered by upstream reset_at timestamp jitter
- **GIVEN** limit warm-up is enabled globally and for an account
- **WHEN** background usage refresh records a newer sample whose `reset_at` advanced by less than 60 seconds as upstream timestamp jitter
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
- **WHEN** an account is paused, deactivated, `reauth_required`, rate-limited, quota-exceeded, or in an auth-refresh failure path
- **THEN** limit warm-up MUST NOT send traffic for that account

#### Scenario: Reset recovery completes before warm-up
- **GIVEN** an opted-in Free account is `rate_limited` and has qualifying monthly reset evidence
- **WHEN** marker-guarded recovery succeeds
- **THEN** the scheduler first persists the account as `active` and clears its block markers
- **AND** only then may it evaluate the same monthly reset tuple for warm-up

#### Scenario: Recovery race prevents warm-up from stale evidence
- **GIVEN** reset evidence makes a blocked account appear recoverable
- **AND** a concurrent write changes its status or block markers before recovery persists
- **WHEN** the recovery compare-and-set misses
- **THEN** the stale scheduler snapshot remains ineligible for warm-up

#### Scenario: Sender rejects an account re-blocked after candidate creation
- **GIVEN** an active account produced a valid warm-up candidate
- **AND** the account becomes blocked before upstream warm-up traffic begins
- **WHEN** the sender reloads the account state
- **THEN** it does not send the warm-up request

#### Scenario: Warm-up attempts are durable and deduplicated
- **WHEN** multiple refresh workers observe the same account/window/reset candidate
- **THEN** the database permits at most one persisted attempt for that tuple
- **AND** later refresh cycles skip that tuple after a prior attempt exists

#### Scenario: Persisted recovery evidence shares the warm-up tuple
- **GIVEN** a scheduler restart causes recovery to use a persisted monthly before/after transition
- **WHEN** the recovered active account reaches warm-up evaluation
- **THEN** warm-up derives the candidate from that same transition's new reset deadline
- **AND** an existing attempt for the account/monthly/reset tuple prevents another send

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
