# data-retention Delta

## MODIFIED Requirements

### Requirement: Retention is opt-in and validated

Retention MUST be disabled by default. Retention windows are resolved per
source with dashboard-first precedence: a non-NULL
`dashboard_settings.request_log_retention_days` /
`dashboard_settings.usage_history_retention_days` value MUST win; when the
dashboard value is NULL the corresponding deprecated env alias
(`CODEX_LB_REQUEST_LOG_RETENTION_DAYS` /
`CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`) MUST apply; when neither is set
retention MUST be disabled. At every layer the value `0` means disabled.

Both the env validators and the dashboard settings API MUST accept `0`
(disabled) or values at or above their safety floors (30 days for request
logs, 45 days for usage history) up to 3650; configurations between 1 and the
floor MUST be rejected — at startup for env values, with a validation error
for dashboard API updates.

#### Scenario: Default configuration deletes nothing

- **GIVEN** neither retention setting is configured in the dashboard or the environment
- **WHEN** the retention job runs
- **THEN** no rows are deleted from `request_logs`, `usage_history`, or `additional_usage_history`

#### Scenario: Unsafe env retention values fail fast

- **WHEN** an operator sets `request_log_retention_days=7` or `usage_history_retention_days=10`
- **THEN** settings validation MUST raise an error at startup naming the violated floor

#### Scenario: Unsafe dashboard retention values are rejected

- **WHEN** a dashboard settings update carries `requestLogRetentionDays=7` or `usageHistoryRetentionDays=10`
- **THEN** the API MUST reject the update with a validation error (the internal validator message names the violated floor with the same wording as the env validator)
- **AND** the stored settings MUST remain unchanged

#### Scenario: Dashboard value overrides the env alias

- **GIVEN** `CODEX_LB_REQUEST_LOG_RETENTION_DAYS=90` and a dashboard value of `30`
- **WHEN** the retention job runs
- **THEN** the request-log cutoff MUST be computed from 30 days

#### Scenario: Dashboard zero disables retention despite the env alias

- **GIVEN** `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS=45` and a dashboard value of `0`
- **WHEN** the retention job runs
- **THEN** no `usage_history` rows are deleted

#### Scenario: Env alias applies while the dashboard value is unset

- **GIVEN** a NULL dashboard value and `CODEX_LB_REQUEST_LOG_RETENTION_DAYS=30`
- **WHEN** the retention job runs
- **THEN** the request-log cutoff MUST be computed from 30 days

### Requirement: Retention runs leader-gated in bounded batches

The retention job MUST run on at most one instance at a time and MUST delete
in bounded batches, each committed in its own transaction, so a large backlog
never holds one long transaction. The scheduler MUST re-evaluate the
effective retention configuration on every tick through the runtime-settings
path, so a dashboard change (enable, disable, or window change) takes effect
without a process restart; ticks whose effective configuration disables
retention MUST NOT run a pass.

#### Scenario: Backlog is pruned incrementally

- **GIVEN** more prunable rows than one batch
- **WHEN** a retention pass runs
- **THEN** rows are deleted across multiple bounded transactions until no prunable rows remain

#### Scenario: Enabling retention from the dashboard needs no restart

- **GIVEN** a running instance with retention disabled
- **WHEN** an operator sets a dashboard retention window
- **THEN** a subsequent scheduler tick runs a retention pass without a restart

#### Scenario: Disabled effective retention skips the pass

- **GIVEN** dashboard and env retention both resolve to 0
- **WHEN** the scheduler ticks
- **THEN** no retention pass runs
