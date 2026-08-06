## MODIFIED Requirements

### Requirement: Shutdown settlement uses the post-drain reserve

Terminal websocket settlement MUST use the unused post-drain cleanup reserve after the shared drain deadline expires, and MUST remain bounded by the combined drain-plus-reserve deadline.

#### Scenario: Exhausted drain retains settlement grace

- **WHEN** shutdown cancellation occurs after the drain deadline has expired
- **THEN** terminal settlement receives the remaining post-drain reserve rather than a zero timeout

### Requirement: Nested shutdown cleanup stays inside its containing window

Lifespan persistence cleanup MUST receive no more than the current remaining drain timeout.

#### Scenario: Recovery settlement cleanup is bounded

- **WHEN** lifespan shutdown drains recovery settlements
- **THEN** its timeout is the live drain remainder and cannot exceed the containing shutdown window

### Requirement: Shutdown drain timeout is bounded

`shutdown_drain_timeout_seconds` MUST be greater than zero and no greater than 300 seconds.

#### Scenario: Invalid drain timeout is rejected

- **WHEN** configuration supplies zero, a negative value, or a value above 300
- **THEN** settings validation fails
