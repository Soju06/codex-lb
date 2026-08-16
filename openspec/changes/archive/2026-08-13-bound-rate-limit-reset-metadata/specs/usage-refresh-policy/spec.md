## ADDED Requirements

### Requirement: Implausible persisted rate-limit deadlines do not block recovery

Background usage refresh MUST treat a persisted `rate_limited` reset deadline
as invalid when it is non-finite, elapsed, or beyond
`RATE_LIMIT_RESET_MAX_HORIZON_SECONDS` (366 days) plus the less-than-one-second
whole-second persistence tolerance. An invalid deadline MUST NOT be treated as
an unexpired explicit cooldown. When the account carries `blocked_at`, recovery
MUST still honor the existing 30-second minimum floor and MUST still require
the existing fresh available quota evidence recorded after the block. Without
`blocked_at`, recent available evidence SHALL suffice. Every applicable quota
window MUST report below `100%` usage before recovery.

#### Scenario: Scheduler recovers an implausible persisted cooldown

- **WHEN** an account is persisted as `rate_limited` with a reset deadline more than 366 days in the future
- **AND** its persisted `blocked_at` minimum floor has elapsed
- **AND** a later background usage refresh writes fresh available quota evidence
- **THEN** the scheduler treats the reset deadline as invalid
- **AND** marks the account `active`
- **AND** clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler preserves a plausible unexpired cooldown

- **GIVEN** an account is persisted as `rate_limited` with a finite reset deadline within 366 days
- **AND** that deadline has not elapsed
- **WHEN** a later background usage refresh writes fresh available quota evidence
- **THEN** the scheduler leaves the account `rate_limited`

#### Scenario: Scheduler recovers an implausible legacy deadline without a block marker

- **GIVEN** an account is persisted as `rate_limited` with an implausible reset deadline and no `blocked_at`
- **WHEN** a later background usage refresh writes recent available quota evidence for every applicable window
- **THEN** the scheduler marks the account `active`
- **AND** clears persisted `reset_at`
