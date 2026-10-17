## ADDED Requirements

### Requirement: Managed HTTP client shutdown wait is bounded

The managed HTTP client close MUST force-close each client and then wait for its completion for at most 10 seconds before shutdown continues to process-death marking and database disposal. When that deadline passes the wait MUST be abandoned rather than cancelled, because each client owns its own close task and cancelling the waiter would not unwind a stalled transport teardown. The shutdown coordinator MUST retain the close task until it settles and consume any terminal exception. A close that fails or cancels itself MUST NOT fail shutdown.

#### Scenario: A stalled transport teardown does not pin shutdown

- **WHEN** a managed HTTP client never reports itself closed
- **THEN** the close step returns once its deadline elapses
- **AND** the abandoned close is left running rather than cancelled
- **AND** shutdown continues to process-death marking and database disposal

#### Scenario: A prompt close is awaited normally

- **WHEN** every managed HTTP client closes before the deadline
- **THEN** the close step returns as soon as they finish

#### Scenario: A failing close does not fail shutdown

- **WHEN** the managed HTTP client close raises
- **THEN** the failure is reported
- **AND** shutdown continues
