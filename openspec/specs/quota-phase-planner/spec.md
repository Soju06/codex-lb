# quota-phase-planner Specification

## Purpose

Define the quota phase planner contracts for audit-only defaults,
phase-aware routing costs, scheduler safety, warmup-effect evidence, and
dashboard/operator controls.

## Requirements

### Requirement: Quota phase planner defaults are non-invasive

The quota phase planner SHALL default to audit-only behavior. Fresh installations
MUST enable routing costs and scheduler audit rows without sending synthetic
traffic, and the planner MUST skip work instead of blocking user traffic when
forecast, usage, or warmup-effect data is stale, missing, or uncertain.

#### Scenario: Fresh installs do not send warmup traffic

- **GIVEN** the service starts with default quota planner settings
- **WHEN** the scheduler evaluates a planner tick
- **THEN** it may write shadow or no-op decision rows
- **AND** it MUST NOT send synthetic warmup traffic

#### Scenario: Uncertain planner data is non-blocking

- **GIVEN** planner input data is stale, missing, or uncertain
- **WHEN** routing or scheduler planning evaluates accounts
- **THEN** real user requests remain eligible according to the normal hard
  account gates
- **AND** scheduler actions are skipped or recorded as audit decisions instead
  of burning quota

### Requirement: Quota phase scheduler uses one async session safely

The quota phase planner scheduler SHALL avoid concurrent database operations on
the same async session. When the scheduler needs primary and secondary usage
snapshots from one session, it MUST issue those repository reads sequentially or
use separate sessions for true parallelism.

#### Scenario: Primary and secondary usage snapshots are read safely

- **GIVEN** a quota planner tick is running inside one background database
  session
- **WHEN** it loads primary and secondary usage snapshots
- **THEN** it reads the snapshots without overlapping operations on that session
- **AND** the tick can continue to build account state, forecasts, simulations,
  and decisions

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
