## ADDED Requirements

### Requirement: Async synchronization primitives survive cancelled-waiter releases

The asyncio `Lock` and `Semaphore` primitives used by the proxy (including every HTTP-bridge session `pending_lock`, the bridge registry lock, and websocket mixin locks) MUST remain acquirable after a release that happens while a queued waiter has already been cancelled but has not yet removed itself from the waiter queue. A release in that window MUST NOT leave the primitive with no owner and a non-empty waiter queue that nobody will ever wake. The repository MUST pin a dependency floor whose primitive implementation satisfies this property and MUST keep a deterministic regression that drives the release/cancel/acquire interleaving.

#### Scenario: Newcomer acquires after a release coinciding with a cancelled waiter

- **GIVEN** task O holds the lock and task W1 is queued behind it
- **WHEN** W1 is cancelled, O releases, and a newcomer A calls acquire before W1 has run its cancellation handler
- **THEN** A acquires the lock within the same bounded wait
- **AND** the lock reports no owner and no waiting tasks after A releases

#### Scenario: Dependency floor pins the fixed primitive

- **WHEN** the project dependencies are resolved
- **THEN** the resolved anyio version is at least 4.14.0
- **AND** the regression covering the cancelled-waiter release interleaving passes
