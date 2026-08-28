# automations Specification

## Purpose
TBD - created by archiving change add-automations-scheduled-pings. Update Purpose after archive.
## Requirements
### Requirement: Automation jobs are manageable via dashboard APIs

The system MUST provide dashboard APIs to create, list, update, enable/disable, delete, and manually trigger automation jobs. A job MUST include name, enabled state, schedule type, schedule time, timezone, schedule days, model, and account targeting.

#### Scenario: Create daily ping job

- **WHEN** an admin submits `POST /api/automations` with a daily schedule (`time`, `timezone`), one model, and explicit `accountIds`
- **THEN** the system persists the job and returns its computed `nextRunAt`
- **AND** `GET /api/automations` includes that job

#### Scenario: Create job targeting all accounts

- **WHEN** an admin submits `POST /api/automations` with `accountIds` omitted or empty
- **THEN** the system persists the job with all-accounts targeting semantics
- **AND** each manual or scheduled cycle resolves currently eligible accounts exactly once when that cycle starts
- **AND** later account status changes do not add new accounts into an already created cycle

#### Scenario: Disable job

- **WHEN** an admin submits `PATCH /api/automations/{id}` with `enabled: false`
- **THEN** the scheduler no longer starts new runs for that job until re-enabled

#### Scenario: Invalid account set is rejected

- **WHEN** an admin creates or updates a job with unknown account IDs
- **THEN** the request is rejected with dashboard `400` error code `invalid_account_ids`

#### Scenario: Invalid timezone is rejected

- **WHEN** an admin creates or updates a job with a non-IANA timezone identifier
- **THEN** the request is rejected with dashboard `400` error code `invalid_schedule_timezone`

#### Scenario: Invalid schedule time is rejected

- **WHEN** an admin creates or updates a job with schedule time not matching `HH:MM` (24-hour)
- **THEN** the request is rejected with dashboard `400` error code `invalid_schedule_time`

#### Scenario: Invalid schedule threshold is rejected

- **WHEN** an admin creates or updates a job with schedule threshold outside `0..240`
- **THEN** the request is rejected with dashboard `400` error code `invalid_schedule_threshold`

### Requirement: Daily schedules support weekday selection and optional dispatch spreading

Daily jobs MUST support explicit weekday selection and optional random dispatch spreading in a post-trigger time window.

#### Scenario: Weekday selection is honored

- **WHEN** a job is configured with `days: ["mon","wed","fri"]`
- **THEN** scheduler execution occurs only on configured weekdays
- **AND** non-configured weekdays are skipped

#### Scenario: Threshold spreads account execution attempts

- **WHEN** a job run starts with `thresholdMinutes > 0` and multiple target accounts
- **THEN** the system assigns randomized per-account offsets within `[0, thresholdMinutes]`
- **AND** it avoids duplicate offsets when possible within that window
- **AND** the persisted dispatch plan for that cycle remains unchanged even if the job is edited before all pending accounts run

### Requirement: Daily schedules execute according to declared timezone

The scheduler MUST execute each enabled daily job once per local calendar day at the configured local time in the configured IANA timezone.

#### Scenario: Timezone-aware execution

- **WHEN** a job is configured for `05:00` in `Europe/Warsaw`
- **THEN** the job executes at 05:00 local Warsaw time every day
- **AND** persisted run metadata stores UTC timestamps for `scheduledFor`, `startedAt`, and `finishedAt`

#### Scenario: DST transition preserves local-time intent

- **WHEN** a DST change occurs in the configured timezone
- **THEN** the next run remains aligned to the configured local clock time for that timezone

#### Scenario: Scheduler restart does not replay stale backlog

- **WHEN** the scheduler is down across multiple missed days
- **THEN** on restart it schedules at most the latest eligible due daily slot
- **AND** it does not enqueue all historical missed slots

#### Scenario: Creating or editing a job after today's local slot does not backfill that same day

- **WHEN** an automation job is created or edited after the configured local-time slot for the current local day has already passed
- **THEN** the scheduler does not start a new same-day catch-up cycle for that job
- **AND** the next automatically created cycle starts at the next eligible future local slot

#### Scenario: Editing a job does not cancel an already created cycle

- **WHEN** a daily cycle has already been created for the current local day
- **AND** the job is edited before every pending account in that cycle has been dispatched
- **THEN** the scheduler continues dispatching the remaining accounts from that existing cycle
- **AND** it does not create a second cycle for the same local-day slot

#### Scenario: Empty persisted scheduled cycle is completed after restart

- **GIVEN** a scheduled cycle snapshot has no eligible accounts and no terminal run row yet
- **WHEN** the scheduler restarts after that due slot
- **THEN** it records one failed scheduled run for that cycle
- **AND** the run uses `no_available_accounts` error details without calling upstream

#### Scenario: Ineligible snapshot accounts are skipped before dispatch

- **GIVEN** a daily cycle snapshot contains an account whose scheduled dispatch time has arrived
- **WHEN** that account is no longer eligible for automation dispatch because it is deleted, rate-limited, quota-exceeded, or deactivated
- **THEN** the scheduler skips that account without creating a per-account failed run
- **AND** the cycle can continue dispatching later eligible accounts from the same snapshot
- **AND** later reactivation of that account does not dispatch it from that same cycle

#### Scenario: Ineligible manual cycle placeholders are omitted before dispatch

- **GIVEN** a manual run cycle pre-created a delayed per-account placeholder
- **WHEN** that account becomes rate-limited, quota-exceeded, deactivated, or deleted before its dispatch time
- **THEN** run details omit that placeholder from pending per-account rows
- **AND** the scheduler skips that placeholder without marking it failed
- **AND** later reactivation of that account does not dispatch that skipped placeholder

### Requirement: Scheduler is safe in multi-replica deployments

The system MUST guarantee at-most-once execution for each due schedule slot `(job_id, scheduled_for)` across replicas.

#### Scenario: Two replicas contend for the same due job

- **WHEN** two scheduler instances observe the same due job
- **THEN** only one instance successfully claims and executes that `(job_id, scheduled_for)` slot
- **AND** the other instance skips execution without creating duplicate run records

#### Scenario: Scheduler run claiming remains deterministic after retries

- **WHEN** one scheduler replica crashes after claiming a slot and before completion
- **THEN** no second replica creates a duplicate claim row for the same slot
- **AND** the existing run row remains the single source of truth for that slot

### Requirement: Account failover is attempted within a job run

When a job run fails on a selected account with retryable account-level failures (for example rate limit, quota exhausted, deactivated account, or upstream auth denial), the system MUST attempt the next configured account for the same run before marking the run failed.

#### Scenario: First account rate-limited, second account succeeds

- **WHEN** account A returns a retryable account-level limit error
- **AND** account B is also configured on the same job
- **THEN** the run retries on account B
- **AND** the run finishes with status `success`

#### Scenario: Exhausted account set yields failed run

- **WHEN** all configured accounts fail with retryable account-level errors during one run
- **THEN** the run finishes with status `failed`
- **AND** the run records terminal `errorCode` and `errorMessage`

#### Scenario: Run succeeds after one or more failed account attempts

- **WHEN** a run succeeds after at least one failed attempt on earlier accounts
- **THEN** the run status is `partial`
- **AND** the run keeps visibility of the terminal successful account

### Requirement: Run outcomes and errors are queryable

The system MUST persist run history rows and expose them via dashboard APIs so operators can inspect success/failure status and error details.

#### Scenario: Failed run surfaces error details

- **WHEN** a run fails after exhausting selected accounts
- **THEN** `GET /api/automations/{id}/runs` returns the run with `status`, `errorCode`, and `errorMessage`
- **AND** the latest run status is available from the job list response

#### Scenario: Manual run is recorded as a distinct trigger type

- **WHEN** an admin calls `POST /api/automations/{id}/run-now`
- **THEN** the system executes the job immediately
- **AND** persisted run history marks `trigger: "manual"`

#### Scenario: Scheduled run is recorded as scheduled trigger type

- **WHEN** the background scheduler executes a due job slot
- **THEN** persisted run history marks `trigger: "scheduled"`

#### Scenario: Run history keeps execution model snapshot

- **GIVEN** a run is claimed while the job uses model `A` and reasoning effort `R`
- **WHEN** an admin later edits that job to model `B`
- **THEN** existing run history still returns model `A` and reasoning effort `R`
- **AND** run-history model filters match the execution model snapshot, not the job's current model

#### Scenario: Deleted accounts do not rewrite completed cycle outcomes

- **GIVEN** a cycle contains a completed per-account run
- **WHEN** that account is deleted afterward
- **THEN** run details still count the per-account run as completed
- **AND** the account does not reappear as a pending dispatch

#### Scenario: Ineligible accounts are omitted from pending cycle details

- **GIVEN** a cycle snapshot contains an account that has not dispatched yet
- **WHEN** that account is no longer eligible for automation dispatch because it is deleted, rate-limited, quota-exceeded, or deactivated
- **THEN** run details omit that account from pending per-account rows
- **AND** the cycle total and pending counts do not include that account

#### Scenario: Run history status filters use visible manual cycle accounts

- **GIVEN** a manual cycle contains a completed per-account run and an ineligible unclaimed placeholder
- **WHEN** an admin filters grouped run history by status
- **THEN** the status filter uses the cycle status computed from visible per-account rows
- **AND** it does not match the cycle as running because of the hidden placeholder

### Requirement: Grouped run-history selection is bounded and snapshot-consistent

The grouped automation run-history repository MUST preserve existing candidate-cycle filtering, representative-run selection, cycle ordering, effective-status calculation, and filter-option semantics on SQLite and PostgreSQL while bounding repeated database work. For a non-empty grouped page, page selection, representative-run loading, and exact total calculation MUST use one database statement so rows and total come from one statement snapshot. When an offset is beyond the final row, the repository MAY execute one additional count statement to preserve the exact-total contract. Account and model filter-option facets MUST be loaded with no more than one database statement; status and trigger options MUST continue to come from their canonical complete enums.

#### Scenario: Common page skips dynamic status aggregation

- **WHEN** grouped run history is requested without an effective-status filter
- **THEN** candidate cycles and their representatives are selected without computing current account eligibility or effective cycle status
- **AND** search, account, model, trigger, and job filters retain their existing candidate-cycle semantics
- **AND** manual and scheduled cycles retain their existing ordering rules

#### Scenario: Status-filtered page keeps dynamic cycle semantics

- **GIVEN** cycle status depends on visible accounts, current eligibility, paused-account policy, pending-window time, completed outcomes, or hidden manual placeholders
- **WHEN** grouped run history is filtered by effective status
- **THEN** the page uses the full dynamic status calculation over each selected candidate cycle
- **AND** the returned cycles match the same effective statuses as before the query refactor

#### Scenario: Non-empty page rows and total share one snapshot

- **WHEN** a grouped run-history offset returns at least one cycle
- **THEN** representative run rows and the exact matching total are returned by one repository database statement
- **AND** the total describes the same statement snapshot as the returned rows

#### Scenario: Offset beyond the final page keeps exact total

- **GIVEN** matching cycle history exists
- **WHEN** the requested offset is beyond the final matching cycle
- **THEN** the response contains no items
- **AND** it reports the exact non-zero total using at most one bounded count fallback

#### Scenario: Representative and ordering semantics survive filtering

- **GIVEN** a cycle contains multiple attempts and only a subset matches the request filters
- **WHEN** the grouped page is selected
- **THEN** the representative is the latest matching run by `started_at` and `id`
- **AND** the cycle order is derived from the complete cycle's established manual or scheduled start rule
- **AND** model filtering uses the execution snapshot rather than a job's later model value

#### Scenario: Filter options use one facet query

- **WHEN** automation run filter options are requested with any supported filter combination
- **THEN** account and model facets are loaded in no more than one repository database statement
- **AND** status and trigger choices remain the canonical complete sets even when stored history is sparse
- **AND** without an effective-status filter, account and model facets are derived directly from matching run rows
- **AND** with an effective-status filter, observed runs or snapshot membership may qualify a cycle before account and model facets expand over all observed run rows in status-matching cycles
- **AND** snapshot-only account IDs are not synthesized into the returned account facet

### Requirement: Automation pings do not mutate durable user continuity

Automation ping execution MUST avoid creating or mutating durable sticky-thread/codex-session continuity used by end-user traffic.

#### Scenario: Automation run does not change sticky-thread routing

- **WHEN** an automation ping run is executed
- **THEN** existing durable sticky-thread/codex-session mappings for user conversations remain unchanged
