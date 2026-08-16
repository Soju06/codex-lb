# Delta Specification: quota-phase-planner

## MODIFIED Requirements

### Requirement: Quota planner API and dashboard expose auditable controls

The quota planner SHALL expose authenticated dashboard APIs and UI controls for
settings, forecast, decisions, warm-now, and cancellation. Settings changes and
scheduler decisions MUST remain auditable, and decision responses SHOULD expose
parsed decision details when stored audit JSON is available. Warm-now reset
eligibility gates MUST compare persisted quota reset epochs against the current
UTC instant regardless of the server process timezone.

#### Scenario: Operators can inspect planner decisions

- **WHEN** a dashboard user requests quota planner decisions
- **THEN** the API returns recent decisions with status, action, account,
  scheduled time, reason, and parsed details when present

#### Scenario: Warm-now uses server-side gates

- **WHEN** a dashboard user requests a manual warm-now probe
- **THEN** the server evaluates the same safety gates used by scheduler
  execution
- **AND** it records a skipped, failed, or executed decision outcome

#### Scenario: Warm-now reset gate is timezone-independent

- **GIVEN** a short-window usage reset epoch is already due in UTC
- **AND** the server process local timezone is UTC+
- **WHEN** a dashboard user requests a manual warm-now probe for that account
- **THEN** the reset gate MUST NOT skip with `account_window_already_active`
  because of process-local timestamp conversion
- **AND** the warm-now request remains eligible for execution when the other
  server-side gates allow it
