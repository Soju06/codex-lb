## MODIFIED Requirements

### Requirement: Background usage refresh reconciles recoverable blocked statuses

Background usage refresh SHALL reconcile persisted `rate_limited` and `quota_exceeded` accounts back to `active` after it writes fresh usage snapshots that prove the blocked window has recovered. This reconciliation SHALL be recovery-only and SHALL NOT promote `active` accounts into blocked statuses. For `rate_limited` accounts, recovery evidence SHALL come from the most recently recorded main-window row: when a post-block refresh no longer reports a short primary window and the last primary sample's own reset deadline has elapsed (or no primary sample exists), a fresh long-window row recorded after the block that still reports usage below `100%` proves recovery. While the last primary sample still claims an unexpired window (or omits reset metadata), or the newer long-window row is itself exhausted, primary freshness SHALL keep gating recovery.

A future persisted `reset_at` SHALL continue to block ordinary recovery except for a `rate_limited` account whose usage history proves that the specific quota window associated with the current block reset. The exception SHALL be bound to a window, not to the account's plan: the scheduler MUST search each quota-window slot (`primary`, `secondary`, `monthly`) for post-block history containing a baseline whose `reset_at` matches the persisted marker within five seconds, and at most one slot can anchor a given block. This exception MUST require `blocked_at` and a future persisted `reset_at`, at least 30 seconds elapsed after `blocked_at`, a baseline recorded strictly after `blocked_at` in the anchored slot, a real temporal reset in an adjacent pair from that same slot at or after that baseline, and both the transition's after sample and the latest sample of that slot recorded after `blocked_at` with usage below `100%`.

Recovery MUST additionally be withheld while a window other than the anchored one reports usage at or above `100%` and has not itself elapsed, considering only the slots the account's plan actually uses: the short window plus `monthly` for Free or `secondary` for paid plans. A window whose own `reset_at` has already passed is stale exhaustion evidence and MUST NOT veto recovery; the zero-capacity Free primary slot is a normalization artifact and is not a window for this rule; and because usage history is append-only, a slot outside the plan's current shape can retain a sample written under a previous plan and MUST NOT be read as current quota state. The reset pair MAY come from the current refresh or be selected from adjacent persisted post-block samples so recovery survives a process restart and tolerates sliding reset deadlines without comparing non-neighboring rows. The persisted lookback MUST be bounded to a fixed number of the most recent rows per slot so the scan cost does not grow with how long the account has been blocked; a transition older than that bound SHALL fall back to the ordinary persisted cooldown. Availability without that matching anchored transition, reset timestamp jitter, an exhausted latest window, or a transition in a slot that does not anchor the persisted marker MUST NOT override the persisted cooldown.

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
- **AND** no qualifying anchored reset transition in any quota window matches the current block
- **THEN** the account stays `rate_limited` until fresh primary evidence arrives, the primary sample's reset deadline elapses, or a qualifying anchored window reset is confirmed

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
- **AND** no qualifying anchored reset transition in any quota window matches the current block
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
- **AND** its current primary usage reports `100%` with an unelapsed reset deadline
- **WHEN** background usage refresh observes available long-window usage or an unrelated reset transition
- **THEN** the account remains `rate_limited`, because the primary window carries plan quota and is still exhausted

#### Scenario: Paid account recovers after an anchored early window reset
- **GIVEN** a Pro account is persisted as `rate_limited` with a future deadline matching its exhausted 7d window
- **AND** upstream re-anchors that window early and reports it at `0%`
- **WHEN** background usage refresh records the post-block transition in that window's slot
- **AND** no other window carrying plan quota is currently exhausted
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: A reset in a slot that does not anchor the block is not evidence
- **GIVEN** an account is persisted as `rate_limited` with a deadline matching one quota window
- **AND** a different quota window records a real reset transition
- **WHEN** background usage refresh evaluates recovery
- **THEN** the unanchored transition is not treated as evidence for the current block
- **AND** the account remains `rate_limited`

#### Scenario: An elapsed exhausted sibling window does not veto recovery
- **GIVEN** an account qualifies for anchored reset recovery in one quota window
- **AND** another window's latest sample reports `100%` but its own reset deadline has already passed
- **WHEN** background usage refresh evaluates recovery
- **THEN** the stale sibling sample does not block the transition
- **AND** the scheduler marks the account `active`

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

#### Scenario: Anchored lookback stays bounded for a long-blocked account
- **GIVEN** an account has been `rate_limited` long enough to accumulate more post-block samples than the lookback bound
- **WHEN** background usage refresh resolves anchored reset evidence
- **THEN** it reads only the most recent bounded slice of that slot's history
- **AND** a qualifying transition inside that slice still recovers the account

#### Scenario: An obsolete sibling row from a previous plan does not veto recovery
- **GIVEN** an account was downgraded from a paid plan to Free
- **AND** its newest `secondary` row is an exhausted, unelapsed sample left over from the paid era
- **WHEN** background usage refresh evaluates anchored monthly reset evidence
- **THEN** the leftover paid sample is not treated as a live exhausted window
- **AND** the scheduler marks the account `active`
