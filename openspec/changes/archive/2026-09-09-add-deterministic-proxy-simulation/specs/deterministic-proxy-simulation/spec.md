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
cancellation delivered after the production terminal claim, at seeded virtual
offsets that reach the account-lease release, the reservation write and the
window after the settlement transfer, so the outcome is checked against
production bookkeeping rather than a re-implementation of it.

"Exactly once" for the API-key reservation is judged on the effective
settlement, the way production judges it: the recorded repository boundary
MUST model the reservation row's `status == "reserved"` compare-and-set per
reservation id (the first completed write is the settlement, any later write
is redundant), MUST run the finalizer's settlement as a detached
scheduler-owned write that survives the caller's cancellation like
`_settle_stream_api_key_usage`, and MUST report the redundant writes in every
snapshot. Redundant release calls on an already settled reservation are a
known production behavior of the detach/terminal races and MUST be pinned by a
strict known-failing test rather than asserted away or folded into the
invariant; the checker MUST however reject an abort-path write on a
reservation the finalizer already settled, which is what the post-settlement
claim guards exist for. The invariant MUST hold under several modelled
write-latency profiles (uniform, reservation write slower, account-lease
release slower), so a pass is not an artifact of one timer ordering.

The checker MUST also assert that the released response-create permit is
observable by a waiting admission request, so the release counters cannot be
satisfied by never releasing; that every task the turn spawned is
scheduler-owned and finished when the schedule ends; and that no pending owned
task carries an abandoned shield callback (asserted on CPython 3.14+, where
`asyncio.shield` leaves that residue; vacuous on 3.13). Failure output MUST
include the seed and the latency profile needed to reproduce the schedule.

The checker MUST also be run against deliberately buggy implementations planted
in production-shaped seams and the tests MUST assert that the checker fails each
of them by the invariant it violates. A coverage test MUST prove that the default
schedule set exercises every settlement path (finalizer, detach backstop, abort
path) and lands the cancellation before the settlement, inside a reservation
write and after the settlement transfer in a meaningful share of schedules, so
the cancellation event cannot silently degrade into a no-op.

Defects the harness surfaces in a pinned dependency MUST be pinned as strict
expected failures conditioned on the dependency version (a minimal
reproduction plus the affected production-turn seeds), so a widened schedule
count or a dependency bump reports the change instead of looking like harness
rot.

#### Scenario: Seeded schedules preserve terminal and release invariants

- **WHEN** the bridge lifecycle checker runs its default schedule set
- **THEN** at least 200 deterministic schedules are exercised
- **AND** every schedule contains all five lifecycle events
- **AND** every accepted schedule settles the API-key reservation through
  exactly one production path (one effective compare-and-set flip), under
  every modelled write-latency profile
- **AND** no abort-path write lands on a reservation the finalizer already
  settled
- **AND** every lease category is released exactly once and ownership converges
  (the request left pending ownership, no settlement claim is left behind,
  every create owner is cleared)
- **AND** every queued admission waiter is admitted
- **AND** every scheduler-owned task has finished and none carries an abandoned
  shield callback

#### Scenario: Real cancellation lands inside production settlement

- **WHEN** a schedule cancels the terminal bookkeeping task after the production
  claim took the request out of pending ownership
- **THEN** production settles the request through the shielded abort path,
  finishes the deferred release it was in, or leaves an already transferred
  settlement alone
- **AND** the coverage test shows the default schedule set exercises the
  finalizer, detach and abort settlement paths and delivers that cancellation
  inside settlement in at least a quarter of the schedules, landing before the
  settlement, inside a reservation write and after the settlement transfer

#### Scenario: Known production redundancy is pinned, not hidden

- **WHEN** a schedule's detach backstop reads the reservation after the
  terminal path already settled it and issues a second release call
- **THEN** the checker records the call as redundant (the modelled
  compare-and-set makes it a no-op) and the schedule still settles exactly once
- **AND** a strict known-failing test asserts that no schedule issues a
  redundant release, so a production change that removes the redundancy is
  reported as an unexpected pass

#### Scenario: Pinned dependency defect is reported as known-failing

- **WHEN** the pinned anyio's asyncio `Lock.release` leaves a cancelled waiter
  queued and a production-turn seed wedges `pending_lock` after the injected
  reader cancellation
- **THEN** a minimal reproduction and the affected seeds fail as strict
  expected failures conditioned on `anyio < 4.14`
- **AND** the default schedule set stays free of those seeds

#### Scenario: Production-shaped canaries are caught

- **WHEN** the same checker runs against a service whose detach releases the
  response-create admission it does not own, whose abort path never settles a
  claimed request, whose reservation release silently does nothing, whose
  post-settlement retry reacquires ownership, or whose lease release re-shields
  a pending task
- **THEN** the checker fails each planted implementation by the invariant it
  violates
