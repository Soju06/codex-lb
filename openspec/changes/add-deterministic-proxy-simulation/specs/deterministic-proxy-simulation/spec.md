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
cancellation, and retry request, the checker MUST assert that the bridge request
reaches exactly one terminal outcome and releases its response-create, API-key,
and account leases exactly once. The checker MUST also assert that the released
response-create permit is observable by a waiting admission request, so the
release counters cannot be satisfied by never releasing. Failure output MUST
include the seed needed to reproduce the schedule.

The checker MUST also be run against a deliberately buggy implementation and the
test MUST assert that the checker fails that implementation.

#### Scenario: Seeded schedules preserve terminal and release invariants

- **WHEN** the bridge lifecycle checker runs its default schedule set
- **THEN** at least 200 deterministic schedules are exercised
- **AND** every schedule contains all four lifecycle events
- **AND** every accepted schedule reaches one terminal outcome
- **AND** every lease category is released exactly once
- **AND** every queued admission waiter is admitted

#### Scenario: Canary double release is caught

- **WHEN** the same checker runs against a toy implementation whose cancel path
  releases the response-create admission and account create lease before the
  shared terminal cleanup takes ownership of them
- **THEN** the checker fails the toy implementation
