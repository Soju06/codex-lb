## MODIFIED Requirements

### Requirement: Admission waits on shared futures scale O(1) per waiter

When multiple requests wait on one shared future (an inflight bridge session creation, a capacity slot, a token-refresh singleflight, or a usage-refresh singleflight), the system MUST use the established shared-future fan-out wait mechanism so attaching a waiter, a waiter timing out, and a waiter being cancelled each perform O(1) work on the shared future. The shared future MUST carry a constant number of done callbacks regardless of waiter count, and the wait mechanism itself MUST NOT cancel or otherwise mutate the shared future or the work it represents when a waiter times out or is cancelled. Admission handlers MAY still settle the shared future explicitly after a waiter's timeout (the http-bridge timeout handler fails and unregisters the inflight future so piled-up waiters converge on one overload outcome); that settlement is an admission-contract decision, not a side effect of waiting. The shared future's result, exception, or cancellation MUST propagate to every waiter with the same semantics as `asyncio.wait_for(asyncio.shield(shared), timeout)`.

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

#### Scenario: Cancelled usage-refresh waiters leave shared refresh running

- **GIVEN** many callers are waiting on one in-flight usage refresh
- **WHEN** all but one caller are cancelled
- **THEN** the cancelled callers detach without adding or removing per-waiter
  callbacks on the shared refresh task
- **AND** the shared refresh continues to completion for the remaining caller

#### Scenario: Non-joining usage refresh starts after its predecessor

- **GIVEN** a usage refresh is already in flight for an account
- **WHEN** another caller requests a non-joining refresh for that account
- **THEN** it waits without cancelling or mutating the in-flight refresh
- **AND** it starts a successor refresh only after the in-flight refresh has
  finished
