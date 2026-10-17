## ADDED Requirements

### Requirement: Admission waits on shared futures scale O(1) per waiter

When multiple requests wait on one shared future (an inflight bridge session
creation, a capacity slot, or a token-refresh singleflight), attaching a
waiter, a waiter timing out, and a waiter being cancelled MUST each perform
O(1) work on the shared future. The shared future MUST carry a constant number
of done callbacks regardless of waiter count, and the wait mechanism itself
MUST NOT cancel or otherwise mutate the shared future or the work it
represents when a waiter times out or is cancelled. Admission handlers MAY
still settle the shared future explicitly after a waiter's timeout (the
http-bridge timeout handler fails and unregisters the inflight future so
piled-up waiters converge on one overload outcome); that settlement is an
admission-contract decision, not a side effect of waiting. The shared future's
result, exception, or cancellation MUST propagate to every waiter with the
same semantics as `asyncio.wait_for(asyncio.shield(shared), timeout)`.

#### Scenario: Waiter pile-up keeps the shared future's callback list constant

- **WHEN** many requests wait on the same inflight bridge-session future
- **THEN** the shared future carries a constant number of done callbacks
- **AND** the callback count does not grow with the number of waiters

#### Scenario: Mass timeout does not degrade the event loop

- **GIVEN** waiters piled onto a shared future that has not resolved within
  the admission wait timeout
- **WHEN** the waiters time out together
- **THEN** each timeout detaches in O(1) without scanning the shared future's
  callback list
- **AND** the surviving admission contract (local-overload `429` with the
  capacity error code) is unchanged

#### Scenario: Client-disconnect storm leaves the owner's creation running

- **WHEN** every waiter on an inflight session future is cancelled by client
  disconnects
- **THEN** the shared future stays pending and the owner's session creation
  continues
- **AND** no per-waiter callbacks remain attached to the shared future
