## ADDED Requirements

### Requirement: Live stream lease release survives caller cancellation

The proxy SHALL prevent caller cancellation from interrupting release after a
Live handler has selected an account stream lease. The handler MUST complete
or explicitly settle the release before propagating the cancellation.

#### Scenario: Cancellation arrives during contended Live lease release

- **GIVEN** a Live handler owns an account stream lease
- **AND** release of that lease has started but is suspended
- **WHEN** caller cancellation is delivered repeatedly while release remains suspended
- **THEN** the release completes exactly once
- **AND** the account slot is returned before cancellation propagates
