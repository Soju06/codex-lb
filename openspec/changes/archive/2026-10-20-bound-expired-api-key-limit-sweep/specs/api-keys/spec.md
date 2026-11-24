## ADDED Requirements

### Requirement: Background limit reset sweep makes forward progress

The background expired-limit sweep MUST attempt each selected limit at most once per invocation, including when the guarded reset update matches no row. For a finite set of limits, it MUST finish without repeatedly selecting skipped rows and MUST continue to later expired limits in bounded batches. It MUST preserve the exact `reset_at` compare-and-set guard, commit each processed batch, and count only successful resets. Limits with `reset_at >= now` MUST remain unchanged.

#### Scenario: An expired row cannot match the timestamp guard

- **GIVEN** an expired SQLite limit has a second-precision `reset_at` that does not equal the bound timestamp representation
- **WHEN** the background sweep runs
- **THEN** the sweep attempts that limit once and terminates without counting or modifying the guarded miss

#### Scenario: A batch of guarded misses precedes resettable limits

- **GIVEN** an entire batch of expired limits fails its guarded updates and later expired limits are resettable
- **WHEN** the background sweep runs
- **THEN** it proceeds beyond the skipped batch and resets the later limits
- **AND** it returns only the number of successful resets and leaves unexpired limits unchanged
