## ADDED Requirements

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

### Requirement: Automation pings do not mutate durable user continuity

Automation ping execution MUST avoid creating or mutating durable sticky-thread/codex-session continuity used by end-user traffic.

#### Scenario: Automation run does not change sticky-thread routing

- **WHEN** an automation ping run is executed
- **THEN** existing durable sticky-thread/codex-session mappings for user conversations remain unchanged
