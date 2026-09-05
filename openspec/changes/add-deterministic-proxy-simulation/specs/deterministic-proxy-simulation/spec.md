## ADDED Requirements

### Requirement: Proxy lifecycle tests can run under virtual time

Proxy lifecycle code that participates in deterministic simulation MUST accept
clock and scheduler collaborators while preserving real-time production defaults.
The scheduler MUST provide sleep, timeout wait, timed multi-future wait,
anyio-style fail-after scope, task creation, drain, and owned-task cancellation
operations. Tests MAY supply a virtual implementation that advances time
explicitly instead of sleeping on the wall clock. Budget arithmetic on the
proxy turn path MUST compare deadlines against the same injected clock that
produced them.

#### Scenario: Production uses real time by default

- **WHEN** proxy services and admission controllers are constructed without test
  collaborators
- **THEN** they use real clock and `asyncio` scheduler behavior
- **AND** no operator setting or runtime configuration is required

#### Scenario: Production uses real primitives verbatim

- **WHEN** no collaborator is injected
- **THEN** each scheduler operation is the corresponding `asyncio`/`anyio` call
  (`sleep`, `wait_for`, `wait`, `create_task`, `fail_after`) with no task
  registry and no extra timeout
- **AND** timed waits keep the control flow of the surrounding production code
  (a future completing in the deadline tick is reported done, as with
  `asyncio.wait`)

#### Scenario: Budget reads follow the injected clock

- **WHEN** a proxy service runs under a virtual clock and a request deadline is
  computed from that clock
- **THEN** the remaining-budget checks on the bridge, websocket and streaming
  retry paths read the same virtual clock
- **AND** advancing the virtual clock to the deadline exhausts the budget without
  any wall-clock time passing

#### Scenario: Test harness advances admission timeout deterministically

- **WHEN** an admission gate is saturated under a virtual scheduler
- **THEN** the test can advance the virtual clock to the configured admission
  timeout
- **AND** the request observes the same local overload behavior without a real
  sleep

### Requirement: Bridge turn lifecycle schedule checking proves exactly-once settlement

The deterministic simulation test suite MUST include a seeded schedule checker
that executes at least 200 schedules by default. Each schedule MUST dispatch its
lifecycle events as concurrent scheduler tasks with seeded virtual wake-up
deadlines, so events sharing a deadline interleave at their await points. For
every schedule interleaving admission wait, upstream terminal event, downstream
cancellation, a cancellation delivered into the in-flight terminal settlement,
and retry request, the checker MUST assert that the bridge request reaches
exactly one terminal outcome and releases its response-create, API-key, and
account leases exactly once. The terminal, downstream-cancel and retry events
MUST run the production settlement code (the bridge upstream-text handler, the
detach backstop and the pre-created retry operation) with only the repository
boundaries recorded, and the settlement cancellation MUST be a real task
cancellation delivered after the production terminal claim, so the outcome is
checked against production bookkeeping rather than a re-implementation of it.
The checker MUST also assert that the released response-create permit is
observable by a waiting admission request, so the release counters cannot be
satisfied by never releasing; that every task the turn spawned is
scheduler-owned and finished when the schedule ends; and that no pending owned
task carries an abandoned shield callback. Failure output MUST include the seed
needed to reproduce the schedule.

The checker MUST also be run against deliberately buggy implementations planted
in production-shaped seams and the tests MUST assert that the checker fails each
of them by the invariant it violates. A coverage test MUST prove that the default
schedule set exercises every settlement path (finalizer, detach backstop, abort
path) and lands the cancellation inside settlement in a meaningful share of
schedules, so the cancellation event cannot silently degrade into a no-op.

#### Scenario: Seeded schedules preserve terminal and release invariants

- **WHEN** the bridge lifecycle checker runs its default schedule set
- **THEN** at least 200 deterministic schedules are exercised
- **AND** every schedule contains all five lifecycle events
- **AND** every accepted schedule settles the API-key reservation through
  exactly one production path
- **AND** every lease category is released exactly once and ownership converges
  (the request left pending ownership, no settlement claim is left behind,
  every create owner is cleared)
- **AND** every queued admission waiter is admitted
- **AND** every scheduler-owned task has finished and none carries an abandoned
  shield callback

#### Scenario: Real cancellation lands inside production settlement

- **WHEN** a schedule cancels the terminal bookkeeping task after the production
  claim took the request out of pending ownership
- **THEN** production settles the request through the shielded abort path or
  finishes the deferred release it was in
- **AND** the coverage test shows the default schedule set exercises the
  finalizer, detach and abort settlement paths and delivers that cancellation
  inside settlement in at least a quarter of the schedules

#### Scenario: Production-shaped canaries are caught

- **WHEN** the same checker runs against a service whose detach releases the
  response-create admission it does not own, whose abort path never settles a
  claimed request, whose reservation release silently does nothing, whose
  post-settlement retry reacquires ownership, or whose lease release re-shields
  a pending task
- **THEN** the checker fails each planted implementation by the invariant it
  violates
