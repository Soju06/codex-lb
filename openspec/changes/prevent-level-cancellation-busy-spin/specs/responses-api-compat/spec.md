## ADDED Requirements

### Requirement: HTTP bridge cleanup ownership survives caller cancellation

Grouped HTTP bridge terminal persistence MUST keep each terminal append barrier and terminal delivery barrier under exactly one strongly owned task until that barrier completes. A barrier MUST NOT be considered released before its callback finishes. If caller cancellation arrives while terminal append, terminal enqueue, or either barrier is pending, the service MUST complete the required terminal delivery and barrier ordering before propagating the original cancellation, and MUST NOT invoke either barrier callback more than once.

After an HTTP bridge session's resource-close owner completes, removal of that exact generation from detached-capacity tracking MUST remain independently owned until registry finalization completes. Cancellation while finalization waits for the bridge registry lock MUST NOT leave a closed generation consuming local bridge capacity, MUST NOT remove another generation, and MUST propagate only after the exact detached generation is finalized.

#### Scenario: Grouped terminal cancellation releases both barriers

- **GIVEN** grouped terminal failure persistence is waiting on a terminal append and its append barrier
- **WHEN** the caller's request scope is cancelled before either operation completes
- **THEN** the append-barrier callback completes exactly once
- **AND** the terminal event and end-of-stream marker are delivered to the downstream queue
- **AND** the delivery-barrier callback completes exactly once
- **AND** the original cancellation propagates only after those ordering points complete

#### Scenario: Cancelled terminal append fails after cancellation

- **GIVEN** caller cancellation has been deferred while a terminal append owner remains pending
- **WHEN** that append fails instead of returning a persistence result
- **THEN** fallback terminal delivery and both barrier ordering points still complete
- **AND** the append failure does not erase the original caller cancellation
- **AND** the original cancellation propagates after fallback delivery completes

#### Scenario: Re-entered barrier release re-awaits one owner

- **GIVEN** a terminal barrier callback has started but remains pending
- **WHEN** cleanup reaches the same barrier release path again after interruption
- **THEN** cleanup awaits the existing barrier owner
- **AND** it does not invoke the barrier callback a second time

#### Scenario: Cancelled detached finalization releases bridge capacity

- **GIVEN** a detached HTTP bridge session has completed resource close
- **AND** registry finalization is waiting for the bridge registry lock
- **WHEN** the session-close caller is cancelled
- **THEN** the finalization owner removes that exact detached generation after acquiring the lock
- **AND** no other bridge generation is removed
- **AND** caller cancellation propagates only after detached-capacity tracking is finalized
