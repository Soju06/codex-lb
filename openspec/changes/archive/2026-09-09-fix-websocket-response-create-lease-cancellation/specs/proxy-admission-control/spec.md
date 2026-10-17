## ADDED Requirements

### Requirement: WebSocket response-create lease cleanup is cancellation-safe

When WebSocket terminal cleanup has captured an account response-create lease, it MUST complete the asynchronous lease release even if the surrounding task is cancelled while waiting for the load-balancer runtime lock. Cleanup MUST retain the existing response-create gate release semantics.

#### Scenario: Cancellation under lease-release contention returns the account slot

- **GIVEN** a WebSocket request owns an account response-create lease and its
  response-create gate
- **AND** the load-balancer runtime lock is held by another task
- **WHEN** terminal cleanup is cancelled while releasing the account lease
- **THEN** the account response-create slot MUST be returned after the lock is
  freed
- **AND** the request state does not retain the released lease
- **AND** the response-create gate cleanup semantics remain unchanged
