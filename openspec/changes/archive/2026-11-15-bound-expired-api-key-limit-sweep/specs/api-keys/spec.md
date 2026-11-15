## ADDED Requirements

### Requirement: Background limit reset sweep makes forward progress

The background expired-limit sweep MUST attempt each selected limit at most once per invocation, including when a guarded reset update matches no row. For a finite set of limits, it MUST terminate without repeatedly selecting skipped rows and MUST continue to later expired limits in bounded batches. It MUST preserve the exact `reset_at` compare-and-set guard, commit each processed batch and count only successful resets. Limits with `reset_at >= now` MUST remain unchanged.

#### Scenario: An expired row cannot match the timestamp guard

- **GIVEN** an expired SQLite limit has a second-precision `reset_at` that does not equal the bound timestamp representation
- **WHEN** the background sweep runs
- **THEN** the sweep attempts that limit once and terminates without counting or modifying the guarded miss

#### Scenario: A skipped batch precedes resettable limits

- **GIVEN** a full batch of expired limits fails its exact timestamp guards
- **AND** later expired limits can be updated
- **WHEN** the background sweep runs
- **THEN** it reaches and resets the later limits, commits each processed batch and counts only successful updates
- **AND** future limits remain unchanged

#### Scenario: There are no expired limits

- **WHEN** the background sweep finds no expired limits
- **THEN** it returns zero without committing a batch
