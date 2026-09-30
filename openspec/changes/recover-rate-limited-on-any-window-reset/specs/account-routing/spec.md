## MODIFIED Requirements

### Requirement: Rate-limit cooldowns are enforced across replicas

A replica that did not observe the upstream 429 MUST NOT transition a `RATE_LIMITED` account to `ACTIVE` while the persisted `reset_at` deadline is in the future unless background usage refresh proves that the exact blocked quota window reset under the strict exception below. For `RATE_LIMITED` rows with `blocked_at` set but no persisted `reset_at` (legacy rows written before cooldown persistence), replicas MUST hold the account `RATE_LIMITED` until at least `blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS`. Recovery transitions MUST be written through the compare-and-set status update (`update_status_if_current`) so a stale snapshot cannot clobber a newer marking.

The reset-confirmed exception SHALL apply to any account with a still-future persisted deadline after the 30-second minimum floor has elapsed, and SHALL be bound to a window rather than to the account's plan. Post-block history in exactly one quota-window slot MUST contain a baseline whose reset deadline matches the persisted account deadline within five seconds, and a deadline matching the baseline of more than one slot MUST disqualify the exception because it does not identify the blocked window, whether or not those slots went on to reset, and an adjacent before/after pair from that same slot at or after that baseline MUST prove a real temporal reset. Both the after sample and the latest sample of that slot MUST be post-block and below `100%`. When the anchored window is not the short window, recovery MUST additionally be withheld while the account's short (`primary`) window reports `100%` and has not itself elapsed, unless that slot's quota capacity for the plan is known to be zero or its row reports a recognized long window's duration; an unrecognized duration MUST count as the short window. This is the only cross-window condition; a long window at `100%` MUST NOT withhold recovery, because credit-backed quota and weekly-shape normalization govern whether it still permits traffic. The recovery compare-and-set MUST match the persisted status, deactivation reason, `reset_at`, and `blocked_at`, then clear both markers when it writes `ACTIVE`. The evidence MAY be loaded from persisted history after a process restart, but availability alone and comparisons between non-neighboring rows MUST NOT satisfy the exception.

This constraint applies to every recovery path that writes account status, including the usage-refresh reconcile path. A usage refresh that observes available quota for a `RATE_LIMITED` account with `blocked_at` set MUST NOT rewrite the account to `ACTIVE` or clear its markers while the effective persisted cooldown is running unless the strict reset-confirmed exception succeeds. The replica that observed the current 429 MAY still recover earlier through its runtime-cooldown-gated fresh-usage path only when its runtime block marker is at least as recent as the effective persisted `blocked_at`; leftover runtime state from an earlier 429 MUST NOT unlock early recovery of a newer block. `RATE_LIMITED` rows without `blocked_at` keep the existing fresh-usage recovery. Generic 429 and Retry-After cooldowns without matching reset evidence, reset timestamp jitter, exhausted post-reset windows, and an exhausted unelapsed short window MUST remain protected until their ordinary recovery condition is met.

#### Scenario: Usage refresh does not clear a running Retry-After cooldown

- **GIVEN** an account marked `RATE_LIMITED` by a 429 whose Retry-After hint persisted `reset_at` 20 minutes in the future and `blocked_at` set
- **WHEN** a periodic usage refresh fetches fresh usage showing available quota before that deadline
- **AND** no qualifying reset transition matches the persisted deadline
- **THEN** the persisted row keeps status `RATE_LIMITED` with its `reset_at` and `blocked_at` intact
- **AND** once the deadline elapses, a later refresh may recover the account to `ACTIVE` through the compare-and-set path

#### Scenario: Confirmed blocked Free monthly reset permits peer recovery

- **GIVEN** replica A marked a Free account `RATE_LIMITED` with `blocked_at` and a persisted deadline matching that account's monthly window
- **AND** the 30-second minimum floor has elapsed
- **WHEN** replica B observes a real post-block transition from the matching monthly baseline into a new available monthly window
- **AND** the latest monthly sample remains below `100%`
- **THEN** replica B may compare-and-set the account to `ACTIVE` before the old persisted deadline
- **AND** a successful transition clears `reset_at` and `blocked_at`

#### Scenario: Confirmed blocked window reset permits peer recovery on any plan

- **GIVEN** replica A marked an account `RATE_LIMITED` with `blocked_at` and a persisted deadline matching one of that account's quota windows
- **AND** the 30-second minimum floor has elapsed
- **WHEN** replica B observes a real post-block transition from the matching baseline into a new available window in that same slot
- **AND** the latest sample of that slot remains below `100%`
- **AND** the account's short window is not itself exhausted and unelapsed
- **THEN** replica B may compare-and-set the account to `ACTIVE` before the old persisted deadline
- **AND** a successful transition clears `reset_at` and `blocked_at`

#### Scenario: Paid account recovers after an early upstream weekly reset

- **GIVEN** a Pro account is `RATE_LIMITED` on its exhausted 7d window with a persisted deadline days in the future
- **AND** the 30-second minimum floor has elapsed
- **AND** upstream re-anchors that 7d window early and reports it at `0%`
- **WHEN** background usage refresh records the post-block transition in that window's slot
- **AND** the account's short window is not itself exhausted and unelapsed
- **THEN** the account may be recovered to `ACTIVE` before the persisted deadline
- **AND** the recovery clears `reset_at` and `blocked_at`

#### Scenario: Generic 429 without matching reset evidence remains protected

- **GIVEN** an account has a future persisted cooldown from an upstream 429 or Retry-After hint
- **AND** fresh usage reports availability but no temporal reset whose baseline matches that deadline
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

- **GIVEN** reset evidence qualifies a blocked account for early recovery
- **AND** another replica changes its status or either block marker before recovery commits
- **WHEN** the recovery compare-and-set evaluates the older snapshot
- **THEN** it does not overwrite the newer account row
- **AND** the account is not made routable from the stale evidence

#### Scenario: Exhausted Plus primary window remains protected

- **GIVEN** a Plus account is `RATE_LIMITED` with primary usage at `100%` and an unelapsed primary reset deadline
- **WHEN** a replica observes available long-window usage or a long-window reset
- **THEN** the reset-confirmed exception does not apply, because the primary window carries quota for the plan and is still exhausted
- **AND** the account remains unavailable until its ordinary recovery condition is met

#### Scenario: Legacy row without reset_at is floored

- **GIVEN** a persisted `RATE_LIMITED` row with `blocked_at` five seconds ago and `reset_at` NULL
- **WHEN** a fresh balancer instance evaluates it during selection
- **THEN** the account stays `RATE_LIMITED` and is not selected
- **AND** once the 30-second floor has elapsed, recovery back to `ACTIVE` is permitted through the compare-and-set path
