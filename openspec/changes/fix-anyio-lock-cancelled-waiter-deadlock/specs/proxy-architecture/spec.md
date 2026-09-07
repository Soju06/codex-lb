## ADDED Requirements

### Requirement: AnyIO synchronization primitives survive cancelled-waiter releases

The proxy uses two families of asyncio synchronization primitives: the AnyIO asyncio-backend `anyio.Lock` and `anyio.Semaphore` (every HTTP-bridge session `pending_lock`, the bridge registry lock, websocket mixin locks, and cache locks) and the stdlib `asyncio.Lock` and `asyncio.Semaphore`. When a release happens while a queued waiter has already been cancelled but has not yet removed itself from the waiter queue, each primitive MUST satisfy the following:

- **Lock**: the release MUST either hand ownership to a live (non-cancelled) queued waiter or leave the lock unowned such that the next `acquire()` call succeeds. A release MUST NOT leave the lock with no owner and a queued waiter that is never woken.
- **Semaphore**: the released permit MUST become available to a live queued waiter or to the next acquirer. A release MUST NOT strand the permit behind a cancelled waiter entry so that waiter progress stops while the permit count is positive.

The stdlib primitives already satisfy this (they skip cancelled waiters on acquire) and serve as the reference behaviour. The repository MUST declare an AnyIO dependency floor whose asyncio-backend implementation satisfies this (anyio 4.14.0 or later) and MUST keep a deterministic regression that drives the release/cancel/acquire interleaving against `anyio.Lock`, `anyio.Semaphore`, and the stdlib reference.

#### Scenario: AnyIO Lock newcomer acquires after a release coinciding with a cancelled waiter

- **GIVEN** task O holds an `anyio.Lock` and task W1 is queued behind it
- **WHEN** W1 is cancelled, O releases, and a newcomer A calls `acquire()` before W1 has run its cancellation handler
- **THEN** A acquires the lock within the same bounded wait
- **AND** the lock reports no owner and no waiting tasks after A releases

#### Scenario: AnyIO Semaphore newcomer acquires a permit after a release coinciding with a cancelled waiter

- **GIVEN** task O holds the only permit of an `anyio.Semaphore(1)` and task W1 is queued behind it
- **WHEN** W1 is cancelled, O releases, and a newcomer A calls `acquire()` before W1 has run its cancellation handler
- **THEN** A acquires the permit within the same bounded wait
- **AND** the semaphore reports its full permit count and no waiting tasks after A releases

#### Scenario: Stdlib reference behaviour is preserved

- **GIVEN** the same interleaving is driven against the stdlib `asyncio.Lock`
- **THEN** the newcomer acquires within the same bounded wait

#### Scenario: Dependency floor pins the fixed primitive

- **WHEN** the project dependencies are resolved
- **THEN** the resolved anyio version is at least 4.14.0
- **AND** the regression covering the cancelled-waiter release interleaving passes for both AnyIO primitives and the stdlib reference
