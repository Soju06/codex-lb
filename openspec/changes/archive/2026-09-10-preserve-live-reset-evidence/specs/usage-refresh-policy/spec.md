## ADDED Requirements

### Requirement: Persisted current reset evidence survives a skipped poll

For the account selected by a background refresh slice, reset-confirmed warm-up SHALL evaluate retained authoritative usage evidence even when the poll writes no new usage. A confirmed reset pair belonging to the latest quota window SHALL remain eligible after additional same-window snapshots or scheduler restart. Recovery MUST require an unexpired reset deadline matching the latest snapshot within the existing five-second deduplication tolerance, and MUST apply current usage availability, current account state, global opt-in, per-account opt-in and plan-applicable window selection. Missing evidence MUST NOT be replaced with an inferred reset.

The durable account/window/reset attempt claim SHALL consume recovered evidence across workers and restarts. Pending, succeeded, failed and skipped attempts MUST all prevent another attempt for the same reset identity. A skipped poll MUST NOT admit initial-Free, paid-to-Free or staggered-idle warm-up, including when a concurrent live snapshot arrives after refresh starts.

#### Scenario: Live ingestion wins the write race

- **GIVEN** an eligible account has a retained confirmed reset pair written through live usage ingestion
- **WHEN** the next scheduled poll skips the fresh account
- **THEN** the scheduler attempts one reset-confirmed warm-up for that current window

#### Scenario: Duplicate observations and restart preserve consumption

- **GIVEN** a reset-confirmed attempt already exists
- **WHEN** duplicate live snapshots, a restarted scheduler or a later poll observes the same reset
- **THEN** no second warm-up attempt or send occurs

#### Scenario: Further live observations preserve the reset pair

- **GIVEN** additional same-window usage snapshots arrive before the scheduler evaluates a confirmed reset
- **WHEN** the scheduler evaluates the current window
- **THEN** it can recover the earlier consecutive pair without relying on a fixed count of newest rows

#### Scenario: Historical evidence does not override current eligibility

- **GIVEN** a retained reset pair exists
- **WHEN** the latest window supersedes that reset, its deadline has expired, current quota is exhausted or below the configured availability gate, or the account opts out
- **THEN** no warm-up is sent from that historical pair

#### Scenario: No transition is invented from incomplete history

- **GIVEN** only an available snapshot or a jitter-only pair remains
- **WHEN** the scheduler evaluates a freshness-skipped account
- **THEN** no reset-confirmed warm-up is sent

#### Scenario: Scheduled resets tolerate missing duration metadata

- **GIVEN** consecutive retained snapshots cross the previous reset deadline and confirm a new available window
- **WHEN** the new snapshot omits duration metadata or its deadline exceeds one nominal window from observation
- **THEN** recovery still evaluates the confirmed pair using the ordinary reset rules

#### Scenario: A delayed deadline stays recoverable after a late restart

- **GIVEN** a confirmed weekly reset has a next deadline one week and 120 seconds after observation
- **AND** subsequent same-window snapshots preserve that identity
- **WHEN** the scheduler restarts one week and 10 seconds after the reset and skips a fresh poll
- **THEN** it still recovers the retained reset pair and attempts one warm-up before the deadline expires

#### Scenario: A live write during a skipped poll does not bootstrap or idle-warm

- **GIVEN** a fresh account has no confirmed reset evidence
- **AND** its scheduled poll skips without writing usage
- **WHEN** a live snapshot arrives after that refresh starts
- **THEN** the snapshot does not trigger initial-Free or staggered-idle warm-up
