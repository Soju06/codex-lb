## ADDED Requirements

### Requirement: Quota phase planner defaults are non-invasive

The quota phase planner SHALL default to audit-only behavior. Fresh
installations MUST allow planner audit rows and forecasts without sending
synthetic traffic, and the planner MUST skip work instead of blocking user
traffic when forecast, usage, or warmup-effect data is stale, missing, or
uncertain.

#### Scenario: Fresh installs do not send warmup traffic

- **GIVEN** the service starts with default quota planner settings
- **WHEN** the scheduler evaluates a planner tick
- **THEN** it may write shadow or no-op decision rows
- **AND** it MUST NOT send synthetic warmup traffic

### Requirement: Quota planner API and dashboard expose auditable controls

The quota planner SHALL expose authenticated dashboard APIs and UI controls for
settings, forecast, decisions, warm-now, and cancellation. Settings changes and
scheduler decisions MUST remain auditable, and decision responses SHOULD expose
parsed decision details when stored audit JSON is available.

#### Scenario: Operators can inspect planner decisions

- **WHEN** a dashboard user requests quota planner decisions
- **THEN** the API returns recent decisions with status, action, account,
  scheduled time, reason, and parsed details when present

#### Scenario: Warm-now uses server-side gates

- **WHEN** a dashboard user requests a manual warm-now probe
- **THEN** the server evaluates the same safety gates used by scheduler
  execution
- **AND** it records a skipped, failed, or executed decision outcome

### Requirement: Warmup decisions are claimed before synthetic traffic

Warmup execution SHALL atomically transition a planned decision to `executing`
before reserving API-key budget or sending synthetic probe traffic. Final
outcomes such as `executed`, `failed`, or API-key skip reasons MUST only update
decisions that are still `executing`. Cancellation MUST only update decisions
that are still queued or skipped and MUST NOT cancel an in-flight `executing`
decision.

#### Scenario: Planned warmup is claimed before probe send

- **GIVEN** a planned warmup decision is eligible to run
- **WHEN** warm-now starts sending the synthetic probe
- **THEN** the persisted decision status is already `executing`
- **AND** a concurrent worker cannot claim the same planned decision

#### Scenario: Executing warmup cannot be canceled

- **GIVEN** a warmup decision is already `executing`
- **WHEN** an operator requests cancellation
- **THEN** the decision remains `executing`
- **AND** the response reports that the decision is not cancelable

### Requirement: Warmup effects require usage evidence

The quota phase planner SHALL only record a warmup effect as observed when a
post-probe usage row is available. Missing post-probe usage evidence MUST NOT
produce an `observed`, `known`, or `high` confidence warmup-effect observation.

#### Scenario: Missing post-probe usage does not unlock automation

- **GIVEN** a warmup probe completes
- **AND** usage refresh does not return a post-probe usage row for the account
- **WHEN** the warmup effect observation is recorded
- **THEN** the observation confidence is stored as `unknown`
- **AND** later automatic synthetic warmup gates do not treat that observation
  as sufficient warmup-effect evidence
