## MODIFIED Requirements

### Requirement: Rate-limit cooldowns are enforced across replicas

A replica that did not observe the upstream 429 MUST NOT transition a `RATE_LIMITED` account to `ACTIVE` while the persisted `reset_at` deadline is in the future unless background usage refresh proves that the exact blocked Free monthly window reset under the strict exception below. For `RATE_LIMITED` rows with `blocked_at` set but no persisted `reset_at` (legacy rows written before cooldown persistence), replicas MUST hold the account `RATE_LIMITED` until at least `blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS`. Recovery transitions MUST be written through the compare-and-set status update (`update_status_if_current`) so a stale snapshot cannot clobber a newer marking.

The reset-confirmed exception SHALL apply only to a Free account with a still-future persisted deadline after the 30-second minimum floor has elapsed. Post-block monthly history MUST contain a baseline whose reset deadline matches the persisted account deadline within five seconds, and an adjacent monthly before/after pair at or after that baseline MUST prove a real temporal reset. Both the after sample and latest monthly sample MUST be post-block and below `100%`. The recovery compare-and-set MUST match the persisted status, deactivation reason, `reset_at`, and `blocked_at`, then clear both markers when it writes `ACTIVE`. The evidence MAY be loaded from persisted history after a process restart, but availability alone and comparisons between non-neighboring rows MUST NOT satisfy the exception.

This constraint applies to every recovery path that writes account status, including the usage-refresh reconcile path. A usage refresh that observes available quota for a `RATE_LIMITED` account with `blocked_at` set MUST NOT rewrite the account to `ACTIVE` or clear its markers while the effective persisted cooldown is running unless the strict reset-confirmed exception succeeds. The replica that observed the current 429 MAY still recover earlier through its runtime-cooldown-gated fresh-usage path only when its runtime block marker is at least as recent as the effective persisted `blocked_at`; leftover runtime state from an earlier 429 MUST NOT unlock early recovery of a newer block. `RATE_LIMITED` rows without `blocked_at` keep the existing fresh-usage recovery. Generic 429 and Retry-After cooldowns without matching reset evidence, reset timestamp jitter, exhausted post-reset windows, and non-Free account exhaustion MUST remain protected until their ordinary recovery condition is met.

#### Scenario: Usage refresh does not clear a running Retry-After cooldown

- **GIVEN** an account marked `RATE_LIMITED` by a 429 whose Retry-After hint persisted `reset_at` 20 minutes in the future and `blocked_at` set
- **WHEN** a periodic usage refresh fetches fresh usage showing available quota before that deadline
- **AND** no qualifying Free monthly reset transition matches the persisted deadline
- **THEN** the persisted row keeps status `RATE_LIMITED` with its `reset_at` and `blocked_at` intact
- **AND** once the deadline elapses, a later refresh may recover the account to `ACTIVE` through the compare-and-set path

#### Scenario: Confirmed blocked Free monthly reset permits peer recovery

- **GIVEN** replica A marked a Free account `RATE_LIMITED` with `blocked_at` and a persisted deadline matching that account's monthly window
- **AND** the 30-second minimum floor has elapsed
- **WHEN** replica B observes a real post-block transition from the matching monthly baseline into a new available monthly window
- **AND** the latest monthly sample remains below `100%`
- **THEN** replica B may compare-and-set the account to `ACTIVE` before the old persisted deadline
- **AND** a successful transition clears `reset_at` and `blocked_at`

#### Scenario: Generic 429 without matching reset evidence remains protected

- **GIVEN** an account has a future persisted cooldown from an upstream 429 or Retry-After hint
- **AND** fresh usage reports availability but no temporal monthly reset whose baseline matches that deadline
- **WHEN** any replica evaluates recovery
- **THEN** the account remains `RATE_LIMITED` until an ordinary recovery condition is met

#### Scenario: Peer replica does not flip a cooling account back

- **GIVEN** balancer instance A marked account X `RATE_LIMITED` from a 429 with no reset metadata
- **AND** account X's recorded usage is below 100%
- **WHEN** a second balancer instance sharing the same database runs account selection
- **THEN** account X is not selected
- **AND** the persisted row remains `RATE_LIMITED` with its `reset_at` deadline intact until the deadline elapses or strict reset-confirmed recovery succeeds

#### Scenario: Stale runtime cooldown does not unlock early recovery of a newer block

- **GIVEN** a replica holds expired runtime cooldown state left over from an earlier 429 of account X
- **AND** account X was since re-marked `RATE_LIMITED` by a peer replica with a newer `blocked_at` and a future persisted `reset_at`
- **WHEN** the replica evaluates account X with usage recorded after the newer `blocked_at`
- **AND** no strict reset-confirmed transition matches the newer block
- **THEN** account X stays `RATE_LIMITED` and is not selected until the persisted deadline elapses

#### Scenario: Concurrent newer block wins the recovery race

- **GIVEN** reset evidence qualifies a blocked Free account for early recovery
- **AND** another replica changes its status or either block marker before recovery commits
- **WHEN** the recovery compare-and-set evaluates the older snapshot
- **THEN** it does not overwrite the newer account row
- **AND** the account is not made routable from the stale evidence

#### Scenario: Exhausted Plus primary window remains protected

- **GIVEN** a Plus account is `RATE_LIMITED` with primary usage at `100%`
- **WHEN** a replica observes available long-window usage or a long-window reset
- **THEN** the Free monthly reset exception does not apply
- **AND** the account remains unavailable until its ordinary recovery condition is met

#### Scenario: Legacy row without reset_at is floored

- **GIVEN** a persisted `RATE_LIMITED` row with `blocked_at` five seconds ago and `reset_at` NULL
- **WHEN** a fresh balancer instance evaluates it during selection
- **THEN** the account stays `RATE_LIMITED` and is not selected
- **AND** once the 30-second floor has elapsed, recovery back to `ACTIVE` is permitted through the compare-and-set path
