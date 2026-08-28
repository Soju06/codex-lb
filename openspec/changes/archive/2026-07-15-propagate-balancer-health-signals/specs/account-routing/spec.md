# account-routing Delta

## MODIFIED Requirements

### Requirement: Upstream rate-limit cooldown honors the Retry-After hint duration

The account cooldown SHALL last for the full duration expressed by a "try
again in" hint on an upstream rate-limit error. The parser SHALL
recognize hour, minute, second, and millisecond units, including their word
forms, and SHALL sum compound hints such as `1h2m3s` into a single duration.
A unit token SHALL be recognized only when it is not immediately followed by
another letter, so an unsupported longer word whose prefix matches a unit (for
example `month`, where `m` prefixes the word) is not mis-read as that shorter
unit. When the hint contains no recognizable unit token, the system SHALL fall
back to the error-count backoff schedule. A rate-limited account SHALL NOT be
re-selected before its cooldown elapses.

When the upstream rate-limit error carries no explicit reset metadata
(`resets_at`/`resets_in_seconds`), the resolved cooldown deadline SHALL be
persisted on the account row (`reset_at`) so the cooldown survives process
restarts and is visible to all replicas sharing the database: a parsed
Retry-After hint deadline SHALL be persisted rounded up to the next whole
second (persistence stores `reset_at` as an integer, so a short or fractional
hint MUST NOT truncate down to an already-elapsed deadline), and when the
cooldown comes from the error-count backoff fallback the persisted deadline
SHALL be at least `RATE_LIMITED_MIN_COOLDOWN_SECONDS` (30 seconds) in the
future. Explicit upstream reset metadata, when present, SHALL continue to be
persisted as-is.
The marking replica's in-process cooldown MAY remain shorter than the
persisted deadline so its existing fresh-usage recovery gate is unchanged.

#### Scenario: Compound minute-and-second hint sets the full cooldown

- **GIVEN** an upstream 429 whose message says "try again in 6m0s"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the account cooldown lasts 360 seconds
- **AND** the account is not re-selected until that cooldown elapses

#### Scenario: Minutes-only hint is honored

- **GIVEN** an upstream 429 whose message says "try again in 20m"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the account cooldown lasts 1200 seconds

#### Scenario: Unparseable hint falls back to backoff

- **GIVEN** an upstream 429 whose message has no recognizable "try again in" duration
- **WHEN** the balancer records the rate limit for the account
- **THEN** the cooldown uses the error-count backoff schedule instead

#### Scenario: Unsupported longer word is not mis-read as a shorter unit

- **GIVEN** an upstream 429 whose message says "try again in 1 month"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the `month` token is not read as a 1-minute hint
- **AND** the cooldown uses the error-count backoff schedule instead

#### Scenario: Cooldown without upstream reset metadata is persisted

- **GIVEN** an upstream 429 that carries no `resets_at`/`resets_in_seconds` metadata and no parseable Retry-After hint
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted account row holds status `RATE_LIMITED` with `blocked_at` set
- **AND** the persisted `reset_at` is at least 30 seconds in the future

#### Scenario: Retry-After hint deadline is persisted

- **GIVEN** an upstream 429 whose message says "try again in 20m" and that carries no reset metadata
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted `reset_at` is approximately 1200 seconds in the future

#### Scenario: Short fractional Retry-After hint is not truncated away

- **GIVEN** an upstream 429 whose message says "try again in 500ms" and that carries no reset metadata
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted integer `reset_at` deadline is strictly in the future
- **AND** peer replicas honor the hinted cooldown instead of reselecting the account immediately

## ADDED Requirements

### Requirement: Rate-limit cooldowns are enforced across replicas

A replica that did not observe the upstream 429 MUST NOT transition a
`RATE_LIMITED` account to `ACTIVE` while the persisted `reset_at` deadline is
in the future, regardless of the account's recorded usage. For `RATE_LIMITED`
rows with `blocked_at` set but no persisted `reset_at` (legacy rows written
before cooldown persistence), replicas MUST hold the account `RATE_LIMITED`
until at least `blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS`. Recovery
transitions MUST be written through the compare-and-set status update
(`update_status_if_current`) so a stale snapshot cannot clobber a newer
marking.

This constraint applies to every recovery path that writes account status,
including the usage-refresh reconcile path: a usage refresh that observes
available quota for a `RATE_LIMITED` account with `blocked_at` set MUST NOT
rewrite the account to `ACTIVE` (or clear `reset_at`/`blocked_at`) while the
persisted cooldown deadline — `reset_at`, or the
`blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS` floor when `reset_at` is
NULL — is still in the future. Only the replica that observed the 429 MAY
recover the account earlier, through its runtime-cooldown-gated fresh-usage
path; a replica's runtime cooldown state counts as observing the current 429
only when its runtime block marker is at least as recent as the effective
persisted `blocked_at` — leftover runtime state from an earlier 429 MUST NOT
unlock early recovery of a newer block. `RATE_LIMITED` rows without
`blocked_at` (stale window-derived markings) keep the existing fresh-usage
recovery.

#### Scenario: Usage refresh does not clear a running Retry-After cooldown

- **GIVEN** an account marked `RATE_LIMITED` by a 429 whose Retry-After hint persisted `reset_at` 20 minutes in the future and `blocked_at` set
- **WHEN** a periodic usage refresh fetches fresh usage showing available quota before that deadline
- **THEN** the persisted row keeps status `RATE_LIMITED` with its `reset_at` and `blocked_at` intact
- **AND** once the deadline elapses, a later refresh may recover the account to `ACTIVE` through the compare-and-set path

#### Scenario: Peer replica does not flip a cooling account back

- **GIVEN** balancer instance A marked account X `RATE_LIMITED` from a 429 with no reset metadata
- **AND** account X's recorded usage is below 100%
- **WHEN** a second balancer instance sharing the same database runs account selection
- **THEN** account X is not selected
- **AND** the persisted row remains `RATE_LIMITED` with its `reset_at` deadline intact until the deadline elapses

#### Scenario: Stale runtime cooldown does not unlock early recovery of a newer block

- **GIVEN** a replica holds expired runtime cooldown state left over from an earlier 429 of account X
- **AND** account X was since re-marked `RATE_LIMITED` by a peer replica with a newer `blocked_at` and a future persisted `reset_at`
- **WHEN** the replica evaluates account X with usage recorded after the newer `blocked_at`
- **THEN** account X stays `RATE_LIMITED` and is not selected until the persisted deadline elapses

#### Scenario: Legacy row without reset_at is floored

- **GIVEN** a persisted `RATE_LIMITED` row with `blocked_at` five seconds ago and `reset_at` NULL
- **WHEN** a fresh balancer instance evaluates it during selection
- **THEN** the account stays `RATE_LIMITED` and is not selected
- **AND** once the 30-second floor has elapsed, recovery back to `ACTIVE` is permitted through the compare-and-set path

### Requirement: Transient balancer health signals are replica-local

Transient error counts, error-backoff windows, drain/probe health tiers, probe success streaks, and in-flight/lease pressure SHALL be maintained per replica
as advisory routing state and SHALL NOT require cross-replica agreement;
persisted account status, `reset_at`, and `blocked_at` transitions are the
only cross-replica health signals. Each replica SHALL converge on its own
observations.

#### Scenario: Peer may route to an account draining elsewhere

- **GIVEN** replica A has drained account X after locally observed transient errors
- **WHEN** replica B, which has recorded no errors for X, performs selection
- **THEN** replica B may select account X
- **AND** replica B backs off independently once its own error threshold for X is reached
