## MODIFIED Requirements

### Requirement: Continuous transcript retention

Operation transcript cleanup MUST run periodically in a leader-gated scheduler
and MUST delete eligible operations in bounded batches. A scheduler pass MUST
stop after it drains the eligible backlog or reaches its fixed batch-count or
wall-clock budget, and a later pass MUST be able to resume from the oldest
remaining eligible operation without changing retention eligibility or owner
fencing. When a successful pass reports likely backlog, the scheduler MUST
retry immediately instead of waiting the normal maintenance interval. When a
leader-election skip or failed pass preserves the backlog signal, the scheduler
MUST use a short bounded catch-up delay. Catch-up passes MUST run only operation
transcript retention rather than accelerating unrelated sticky, bridge-session,
or ring maintenance.
Disabling the existing sticky-session mapping cleanup switch MUST NOT disable
operation transcript retention; that switch MAY skip sticky mapping
maintenance while durable operation retention continues.

#### Scenario: Retention drains a small backlog

- **WHEN** fewer operations are eligible than one scheduler-pass budget
- **THEN** the pass removes every eligible operation
- **AND** reports that no backlog is known to remain

#### Scenario: Retention drains all eligible batches

- **GIVEN** every eligible operation fits within one scheduler-pass budget
- **WHEN** transcript retention runs
- **THEN** that pass removes every eligible batch

#### Scenario: Retention yields with a large backlog

- **GIVEN** more operations are eligible than one scheduler-pass budget
- **WHEN** the pass reaches its batch-count or wall-clock budget
- **THEN** it commits the completed bounded batches and stops
- **AND** a later leader-gated pass resumes deletion from the oldest remaining
  eligible operation

#### Scenario: Successful backlog schedules immediate catch-up

- **GIVEN** a cleanup pass stops on a full final batch at its count or time
  budget
- **WHEN** the scheduler chooses the next delay
- **THEN** it retries immediately rather than waiting the normal maintenance
  interval
- **AND** the catch-up pass does not rerun unrelated maintenance

#### Scenario: Skipped or failed backlog uses bounded retry

- **GIVEN** the backlog signal remains after leader-election skips or a failed
  retention pass
- **WHEN** the scheduler chooses the next delay
- **THEN** it uses the bounded backlog retry delay

#### Scenario: Sticky cleanup toggle does not disable transcript retention

- **WHEN** sticky-session cleanup is disabled and the durable bridge schema is
  available
- **THEN** the leader-gated scheduler still deletes bounded batches of expired
  operation transcript rows while skipping sticky mapping cleanup
