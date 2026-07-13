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

### Requirement: Quota planner decisions persist naive UTC instants

The quota phase planner SHALL normalize timezone-aware datetimes to naive UTC
before persisting them to the timezone-naive `QuotaPlannerDecision.scheduled_at`
and `executed_at` columns. When a planned or executed instant is timezone-aware,
the persisted column value MUST equal that instant converted to UTC with its
`tzinfo` removed, preserving the absolute instant. Naive datetimes MUST be
persisted unchanged. JSON audit snapshots MAY continue to record the same
instants as ISO-8601 strings that include a timezone offset.

#### Scenario: Aware planned instant is stored as naive UTC

- **GIVEN** the scheduler logs a decision with a timezone-aware UTC
  `scheduled_at`
- **WHEN** the repository persists the decision row
- **THEN** the stored `scheduled_at` is timezone-naive
- **AND** it equals the original instant expressed in UTC

#### Scenario: Aware executed instant is stored as naive UTC on update

- **GIVEN** a decision is updated with a timezone-aware UTC `executed_at`
- **WHEN** the repository writes the status update
- **THEN** the stored `executed_at` is timezone-naive
- **AND** it equals the original instant expressed in UTC

#### Scenario: Naive instants persist unchanged

- **GIVEN** a decision is logged or updated with a timezone-naive datetime
- **WHEN** the repository persists the value
- **THEN** the stored value is unchanged and remains timezone-naive

### Requirement: Planner repository datetime boundaries are UTC-normalized

Quota phase planner repository methods MUST normalize timezone-aware datetime
inputs to naive UTC before binding those values into database comparisons or
persisted planner observation timestamps.

#### Scenario: Aware datetimes are accepted at repository boundaries

- **GIVEN** quota planner repository calls receive timezone-aware datetime
  values for warmup decision queries, demand aggregation, or quota window
  observations
- **WHEN** those calls bind the values into database statements
- **THEN** the bound values use naive UTC timestamps
- **AND** the queries return rows that match the equivalent UTC instant

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

