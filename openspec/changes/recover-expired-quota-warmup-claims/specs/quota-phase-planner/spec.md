# Delta Specification: quota-phase-planner

## MODIFIED Requirements

### Requirement: Warmup decisions are claimed before synthetic traffic

Warmup execution SHALL atomically transition a planned decision to `executing`
before reserving API-key budget or sending synthetic probe traffic, and that
transition SHALL be the single authoritative enforcement point for the daily
warmup count and credit budgets: the claim statement MUST evaluate either the
`planned` precondition or an expired `executing` warmup claim plus both budget
guards atomically, so concurrent claimants on other replicas or processes
cannot exceed either budget. The count-budget guard MUST include in-flight
`executing` warmup decisions in addition to `executed` ones, so a probe
reserves budget when it is claimed rather than after it completes. The claim
MUST record its own timestamp on the decision, and in-flight `executing`
decisions MUST count against the budget day in which they were claimed — not
the day the decision row was created — so a decision planned before the daily
boundary but claimed after it consumes the claim day's budget. The claim lease
TTL MUST be at least the configured Responses stream request budget so a
healthy warmup probe cannot lose its claim mid-flight. On PostgreSQL,
concurrent claims MUST be serialized (a transaction-scoped advisory lock on a
fixed warmup-budget key) so two claims cannot both evaluate the budget against
a stale snapshot; on SQLite the claim MUST execute as a single statement under
the database-level writer lock. When a claim is refused because a budget guard
failed, the decision MUST be skipped with a reason that distinguishes the
exhausted count budget from the exhausted credit budget. Final outcomes such as
`executed`, `failed`, or API-key skip reasons MUST only update decisions that
are still `executing`. Cancellation MUST only update decisions that are still
`planned` or `skipped` and MUST NOT cancel an in-flight `executing` decision.
Every warmup decision MUST reuse one deterministic warmup request identity
across retries so a reclaimed claim can reconcile from an already-committed
request log instead of sending a duplicate probe.

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

#### Scenario: Warmup claim lease covers the probe budget

- **GIVEN** the configured Responses stream request budget exceeds five minutes
- **WHEN** warm-now claims a warmup decision
- **THEN** the persisted `lease_expires_at` is at least one full stream budget
  after the claim timestamp

#### Scenario: Reclaimed decision settles from a committed success log

- **GIVEN** a warmup decision's prior attempt already committed a successful
  warmup request log
- **AND** the process died before the final decision-status update committed
- **WHEN** a later replica reclaims the expired `executing` decision
- **THEN** it marks the decision `executed` from the durable request-log
  evidence
- **AND** it MUST NOT send a second synthetic probe

#### Scenario: Executing warmup cannot be canceled

- **GIVEN** a warmup decision is already `executing`
- **WHEN** an operator requests cancellation
- **THEN** the decision remains `executing`
- **AND** the response reports that the decision is not cancelable

## ADDED Requirements

### Requirement: Scheduler reconciles expired warmup claims independently of current actions

The quota planner scheduler SHALL reconcile expired `executing` warmup claims
even when the current planner output contains no matching warmup action. This
reconciliation sweep MUST run before the scheduler decides that a tick is a
pure no-op, and it MUST reuse the same warm-now recovery contract so manual
warm-now decisions and no-longer-planned scheduled decisions can self-heal.

#### Scenario: Expired manual warmup claim is reconciled without a new planner action

- **GIVEN** an expired `executing` warmup decision created by a manual warm-now
- **AND** the current planner output contains no action for that decision
- **WHEN** the scheduler runs its next leader tick
- **THEN** the expired claim is still reconciled
- **AND** the decision no longer remains stuck in `executing`
