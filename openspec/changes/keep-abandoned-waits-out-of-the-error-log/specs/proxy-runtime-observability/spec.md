## ADDED Requirements

### Requirement: Abandoned shared waits do not surface as unretrieved-exception errors

Work awaited through the shared-future waiter helper MUST have its failure consumed when it completes, whether or not a waiter is still attached at that moment. A waiter that times out detaches, so a shared future can fail with none attached; that MUST NOT produce an `asyncio` `Task exception was never retrieved` record, which is reserved for exceptions no owner ever received. Every waiter MUST still observe the same result, exception, or cancellation it would otherwise have observed.

#### Scenario: A failure that lands between waits is not reported as unretrieved

- **GIVEN** work awaited through the shared-future waiter helper
- **AND** its only waiter has timed out and detached
- **WHEN** the work then fails
- **THEN** no `Task exception was never retrieved` record is emitted for it
- **AND** the failure is still readable by anything that asks for it afterwards

#### Scenario: A stream that ends while the consumer is away stays out of the error log

- **GIVEN** a streaming Responses request whose keepalive interval has elapsed, so a keepalive frame has been emitted and the chunk pull is unattended
- **WHEN** the upstream source ends before the consumer asks for the next chunk
- **THEN** the request completes normally
- **AND** no error-level record is emitted for the end of that stream

#### Scenario: A live waiter still receives the failure

- **GIVEN** work awaited through the shared-future waiter helper with a waiter attached
- **WHEN** the work fails
- **THEN** the waiter receives that exception
