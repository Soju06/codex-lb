## ADDED Requirements

### Requirement: Shutdown settlement uses the post-drain reserve

Terminal websocket settlement MUST use the unused post-drain cleanup reserve after the shared drain deadline expires, and MUST remain bounded by the combined drain-plus-reserve deadline.

#### Scenario: Exhausted drain retains settlement grace

- **WHEN** shutdown cancellation occurs after the drain deadline has expired
- **THEN** terminal settlement receives the remaining post-drain reserve rather than a zero timeout

### Requirement: Nested shutdown cleanup stays inside its containing window

Lifespan persistence cleanup MUST receive a live remaining deadline and MUST NOT receive the full configured drain timeout, which would nest a fresh copy of the whole budget inside the containing shutdown window.

Recovery-settlement cleanup runs after the drain barrier, so it MUST be bounded by the remaining combined drain-plus-reserve deadline — the same budget terminal settlement draws on. Persistence cleanup that runs after bridge teardown MUST be bounded by the remaining drain timeout.

#### Scenario: Recovery settlement cleanup keeps a budget after an exhausted drain

- **WHEN** lifespan shutdown drains recovery settlements after the drain deadline has expired
- **THEN** its timeout is the remaining drain-plus-reserve deadline rather than a zero drain remainder, and cannot exceed the containing shutdown window

#### Scenario: Post-teardown cleanup is bounded by the drain remainder

- **WHEN** lifespan shutdown drains persistence tasks after HTTP bridge teardown
- **THEN** its timeout is the live drain remainder and cannot exceed the containing shutdown window

### Requirement: Shutdown drain timeout is bounded

`shutdown_drain_timeout_seconds` MUST be greater than zero and no greater than 300 seconds.

#### Scenario: Invalid drain timeout is rejected

- **WHEN** configuration supplies zero, a negative value, or a value above 300
- **THEN** settings validation fails
