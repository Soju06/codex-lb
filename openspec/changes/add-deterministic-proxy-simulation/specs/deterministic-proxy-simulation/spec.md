## ADDED Requirements

### Requirement: Proxy lifecycle tests can run under virtual time

Proxy lifecycle code that participates in deterministic simulation MUST accept
clock and scheduler collaborators while preserving real-time production defaults.
The scheduler MUST provide sleep, timeout wait, task creation, drain, and
owned-task cancellation operations. Tests MAY supply a virtual implementation
that advances time explicitly instead of sleeping on the wall clock.

#### Scenario: Production uses real time by default

- **WHEN** proxy services and admission controllers are constructed without test
  collaborators
- **THEN** they use real clock and `asyncio` scheduler behavior
- **AND** no operator setting or runtime configuration is required

#### Scenario: Test harness advances admission timeout deterministically

- **WHEN** an admission gate is saturated under a virtual scheduler
- **THEN** the test can advance the virtual clock to the configured admission
  timeout
- **AND** the request observes the same local overload behavior without a real
  sleep

### Requirement: Bridge turn lifecycle schedule checking proves exactly-once settlement

The deterministic simulation test suite MUST include a seeded schedule checker
that executes at least 200 schedules by default. For every schedule interleaving
admission wait, upstream terminal event, downstream cancellation, and retry
request, the checker MUST assert that the bridge request reaches exactly one
terminal outcome and releases its response-create, API-key, and account leases
exactly once. Failure output MUST include the seed needed to reproduce the
schedule.

The checker MUST also be run against a deliberately buggy implementation and the
test MUST assert that the checker fails that implementation.

#### Scenario: Seeded schedules preserve terminal and release invariants

- **WHEN** the bridge lifecycle checker runs its default schedule set
- **THEN** at least 200 deterministic schedules are exercised
- **AND** every accepted schedule reaches one terminal outcome
- **AND** every lease category is released exactly once

#### Scenario: Canary double release is caught

- **WHEN** the same checker runs against a toy implementation that releases
  leases twice after cancellation and a late upstream terminal event
- **THEN** the checker fails the toy implementation
