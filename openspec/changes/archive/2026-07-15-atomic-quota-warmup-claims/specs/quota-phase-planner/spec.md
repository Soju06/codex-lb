# Delta Specification: quota-phase-planner

## MODIFIED Requirements

### Requirement: Warmup decisions are claimed before synthetic traffic

Warmup execution SHALL atomically transition a planned decision to `executing`
before reserving API-key budget or sending synthetic probe traffic, and that
transition SHALL be the single authoritative enforcement point for the daily
warmup count and credit budgets: the claim statement MUST evaluate the
`planned` status precondition and both budget guards atomically, so concurrent
claimants on other replicas or processes cannot exceed either budget. The
count-budget guard MUST include in-flight `executing` warmup decisions in
addition to `executed` ones, so a probe reserves budget when it is claimed
rather than after it completes. The claim MUST record its own timestamp on the
decision, and in-flight `executing` decisions MUST count against the budget
day in which they were claimed — not the day the decision row was created —
so a decision planned before the daily boundary but claimed after it consumes
the claim day's budget. On PostgreSQL, concurrent claims MUST be
serialized (a transaction-scoped advisory lock on a fixed warmup-budget key)
so two claims cannot both evaluate the budget against a stale snapshot; on
SQLite the claim MUST execute as a single statement under the database-level
writer lock. When a claim is refused because a budget guard failed, the
decision MUST be skipped with a reason that distinguishes the exhausted count
budget from the exhausted credit budget. Final outcomes such as `executed`,
`failed`, or API-key skip reasons MUST only update decisions that are still
`executing`. Cancellation MUST only update decisions that are still `planned`
or `skipped` and MUST NOT cancel an in-flight `executing` decision.

#### Scenario: Planned warmup is claimed before probe send

- **GIVEN** a planned warmup decision is eligible to run
- **WHEN** warm-now starts sending the synthetic probe
- **THEN** the persisted decision status is already `executing`
- **AND** a concurrent worker cannot claim the same planned decision

#### Scenario: Concurrent claims cannot exceed the daily count budget

- **GIVEN** two replicas each hold a planned warmup decision
- **AND** one warmup remains in the daily count budget
- **WHEN** both replicas execute warm-now concurrently
- **THEN** exactly one decision transitions to `executing` and sends a probe
- **AND** the other decision is skipped with reason
  `daily_warmup_count_budget_exhausted`

#### Scenario: In-flight executing warmups reserve count budget

- **GIVEN** a warmup decision claimed today is still `executing`
- **AND** the daily count budget allows one warmup
- **WHEN** another replica attempts to claim a planned warmup decision
- **THEN** the claim is refused
- **AND** the planned decision does not transition to `executing`

#### Scenario: Warmup planned yesterday but claimed today consumes today's budget

- **GIVEN** a warmup decision the scheduler persisted before the daily boundary
  with a future `scheduled_at`
- **AND** the daily count budget allows one warmup
- **WHEN** the decision is claimed after the daily boundary
- **THEN** the claimed `executing` decision counts against the new day's budget
- **AND** a subsequent claim of another planned decision on the same day is
  refused

#### Scenario: Claim is refused when the credit budget is spent

- **GIVEN** warmup request logs recorded today already meet the daily credit
  budget
- **WHEN** a planned warmup decision is claimed after its execution gate read
  stale budget state
- **THEN** the claim is refused before any probe is sent
- **AND** the decision is skipped with reason
  `daily_warmup_credit_budget_exhausted`

#### Scenario: Executing warmup cannot be canceled

- **GIVEN** a warmup decision is already `executing`
- **WHEN** an operator requests cancellation
- **THEN** the decision remains `executing`
- **AND** the response reports that the decision is not cancelable

## ADDED Requirements

### Requirement: Concurrent decision logging converges on idempotency keys

The planner decision log SHALL treat `idempotency_key` as a convergence point:
when multiple writers concurrently record a decision with the same idempotency
key, exactly one decision row SHALL persist and every writer MUST receive that
surviving row. A duplicate-key collision MUST NOT surface as an unhandled
integrity error that aborts the remainder of a planning tick.

#### Scenario: Two replicas log the same decision key concurrently

- **GIVEN** two planner replicas derive the same decision idempotency key
- **WHEN** both log the decision concurrently against one database
- **THEN** exactly one decision row persists for that key
- **AND** both replicas receive the surviving decision without raising

#### Scenario: Duplicate keys do not abort the planning tick

- **GIVEN** a planning tick logs decisions for several due accounts
- **WHEN** one decision's idempotency key was concurrently inserted by another
  writer
- **THEN** the tick continues logging the remaining accounts' decisions
