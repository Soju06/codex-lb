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

### Requirement: Phase planning is scoped to short rolling windows

An account SHALL be phase-plannable for scheduler warmup candidacy and cold-start routing costs only when its selection state carries a primary window sample with duration metadata of at most 24 hours (weekly and monthly windows are not phase-plannable; samples without duration metadata are not phase-plannable). The warm-up execution gate and warmup-effect observed confidence SHALL treat positive evidence of a long window in the primary slot as disqualifying, while absent samples or samples without duration metadata keep legacy bootstrap behavior. Active/cold window state SHALL derive from the primary window sample's reset timestamp, not from blocked-status reset markers, and only phase-plannable accounts SHALL count as having active phase windows — a long unremapped primary sample MUST NOT feed expiring-window bonuses, active-reset stagger anchors, or simulated pool capacity.

#### Scenario: Weekly-only account is not planned

- **GIVEN** an account whose usage reports only a weekly window
- **WHEN** the planner evaluates warmup candidates and routing costs
- **THEN** the account receives no warmup actions and no cold-start costs

#### Scenario: Mixed pool plans only short-window accounts

- **GIVEN** one account with a 300-minute primary window sample and one account with only weekly usage
- **WHEN** the planner plans warmups during the prewarm band
- **THEN** only the short-window account is considered

#### Scenario: Healthy active window is not treated as cold

- **GIVEN** a healthy `active` account whose primary window sample has an unexpired reset timestamp
- **WHEN** the planner builds routing costs
- **THEN** the account is treated as having an active window
- **AND** it receives no cold-start cost

#### Scenario: Execution gate refuses accounts with a long-window primary sample

- **WHEN** a warm-now request targets an account whose latest primary-slot sample reports a weekly or monthly window duration
- **THEN** the execution gate refuses with a stable `no_short_window` reason

#### Scenario: Execution gate refuses superseded short-window samples

- **GIVEN** an account whose latest primary-slot sample reports a short window
- **AND** a strictly newer long-window row proves a later refresh no longer reported the short window
- **WHEN** a warm-now request targets that account
- **THEN** the execution gate refuses with `no_short_window`

#### Scenario: Superseded short-window samples lose plannability

- **GIVEN** an account whose primary sample is strictly older than its long-window row
- **WHEN** selection state is built
- **THEN** the derived short-window duration is cleared whether or not the stale sample's reset has elapsed
- **AND** the planner does not treat the account as having a short phase window

### Requirement: Phase math derives from observed window durations

Candidate warmup start times, planned window resets, pool-simulation window spans, and synchronization penalties SHALL use each account's observed primary window duration. Planner actions SHALL carry the window duration they were planned with, and cross-account reset math SHALL use each action's own duration.

#### Scenario: Peak alignment uses the observed duration

- **GIVEN** an account whose primary window sample reports a 60-minute duration
- **WHEN** the planner derives candidate start times for a forecast peak
- **THEN** peak-aligned candidates subtract 60 minutes rather than 5 hours
- **AND** the planned action records a 60-minute window duration

