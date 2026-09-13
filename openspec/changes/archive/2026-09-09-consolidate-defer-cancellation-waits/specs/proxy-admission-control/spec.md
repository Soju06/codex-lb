## MODIFIED Requirements

### Requirement: Admission waits on shared futures scale O(1) per waiter

When multiple requests wait on one shared future (an inflight bridge session creation, a capacity slot, a token-refresh singleflight, or a usage-refresh singleflight), the system MUST use the established shared-future fan-out wait mechanism so attaching a waiter, a waiter timing out, and a waiter being cancelled each perform O(1) work on the shared future. The shared future MUST carry a constant number of done callbacks regardless of waiter count, and the wait mechanism itself MUST NOT cancel or otherwise mutate the shared future or the work it represents when a waiter times out or is cancelled. Admission handlers MAY still settle the shared future explicitly after a waiter's timeout (the http-bridge timeout handler fails and unregisters the inflight future so piled-up waiters converge on one overload outcome); that settlement is an admission-contract decision, not a side effect of waiting. The shared future's result, exception, or cancellation MUST propagate to every waiter with the same semantics as `asyncio.wait_for(asyncio.shield(shared), timeout)`.

The same bounded-callback contract applies to waits that re-attach to one owned task repeatedly: defer-cancellation waits on owned cleanup tasks, repeated timed waits such as SSE keepalive ticks on a pending chunk task, and bounded teardown drains. A defer-cancellation wait MUST shield itself from level-cancelled scopes so re-delivered cancellation cannot busy-spin the wait loop, MUST keep the owned task's done-callback count bounded by a constant regardless of how many times the waiter is cancelled or times out, MUST NOT cancel the owned task, MUST defer the caller's cancellation until the owned task finishes and then surface it, and MUST propagate the owned task's cancellation and exceptions unchanged.

Every defer-cancellation wait MUST route through the one canonical shared-future helper (or, where site-specific control flow forces an inline loop, wait through the shared-future fan-out mechanism inside that loop) rather than a hand-rolled `asyncio.shield` retry. The deferred-cancellation surfacing above applies uniformly: a caller consuming a boolean or exception marker from any defer-cancellation wait MUST receive the marker for a level-cancelled scope as well as for edge task cancellation, so cleanup-then-cancel sequencing does not depend on which copy of the wait a call site reached.

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

#### Scenario: Level-cancelled scope cannot spin a defer-cancellation wait

- **GIVEN** a cleanup task owned by a defer-cancellation wait is still running
- **WHEN** the waiter's scope is level-cancelled (client disconnect) while the
  cleanup task remains pending
- **THEN** the wait loop does not busy-spin on re-delivered cancellation
- **AND** the cleanup task's done-callback count stays bounded by a constant
- **AND** the cleanup task runs to completion before the caller's cancellation
  is surfaced

#### Scenario: Keepalive ticks leave the pending chunk task's callbacks bounded

- **GIVEN** an SSE stream whose upstream produces no chunk for many keepalive
  intervals
- **WHEN** each keepalive tick's timed wait on the pending chunk task times out
- **THEN** keepalive frames are emitted and the pending chunk task is not
  cancelled
- **AND** the pending chunk task's done-callback count does not grow with the
  number of elapsed ticks

#### Scenario: Every defer-cancellation wait shares the canonical implementation

- **WHEN** any module performs a defer-cancellation wait on an owned task
- **THEN** the wait routes through the canonical shared-future helper (or the
  shared-future fan-out mechanism inside a site-specific loop)
- **AND** no hand-rolled `asyncio.shield` retry loop remains

#### Scenario: Level cancellation surfaces through every marker shape

- **GIVEN** a caller in a level-cancelled scope awaiting an owned cleanup via
  a defer-cancellation wait that reports a boolean or exception marker
- **WHEN** the owned cleanup completes
- **THEN** the marker reports the deferred cancellation
- **AND** the caller can re-raise it deterministically after cleanup instead
  of being interrupted at an arbitrary later checkpoint
