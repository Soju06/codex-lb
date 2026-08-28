## MODIFIED Requirements

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

## ADDED Requirements

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
