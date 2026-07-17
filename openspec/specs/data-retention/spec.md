# data-retention Specification

## Purpose

Define opt-in retention of request logs and usage history — dashboard-first configuration, safety floors, and pruning invariants — so aged rows can be deleted without losing lifetime totals, latest-known usage, or unfolded data.
## Requirements
### Requirement: Retention is opt-in and validated

Retention MUST be disabled by default. Retention windows are resolved per
source with dashboard-first precedence: a non-NULL
`dashboard_settings.request_log_retention_days` /
`dashboard_settings.usage_history_retention_days` value MUST win; when the
dashboard value is NULL the corresponding deprecated env alias
(`CODEX_LB_REQUEST_LOG_RETENTION_DAYS` /
`CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`) MUST apply; when neither is set
retention MUST be disabled. At every layer the value `0` means disabled.

The dashboard settings API MUST expose, per retention window, the read-only
*effective* value (`requestLogRetentionDays` / `usageHistoryRetentionDays`)
alongside the nullable stored *override*
(`requestLogRetentionOverrideDays` / `usageHistoryRetentionOverrideDays`,
`null` = inherit). Updates MUST use only the override fields with tri-state
semantics: a field absent from the payload leaves the stored value unchanged;
a field present with `null` MUST clear the override back to inherit; a field
present with a value MUST store it as the override — including a value equal
to the current effective (env-inherited) value, which deliberately captures
it as a dashboard override. Because overrides round-trip verbatim (null in,
null out), a full GET-then-PUT save echoing the override fields unchanged
MUST NOT alter the stored values.

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

- **WHEN** a dashboard settings update carries `requestLogRetentionOverrideDays=7` or `usageHistoryRetentionOverrideDays=10`
- **THEN** the API MUST reject the update with a validation error (the internal validator message names the violated floor, mirroring the env validator's wording)
- **AND** the stored settings MUST remain unchanged

#### Scenario: Full-save echoes round-trip inherit unchanged

- **GIVEN** no dashboard override exists and an env alias supplies the effective retention
- **WHEN** a client performs a full GET-then-PUT save echoing `requestLogRetentionOverrideDays: null` back
- **THEN** the stored value remains `NULL = inherit`, so later changes to the deprecated env alias still take effect

#### Scenario: An explicit override equal to the env alias is stored

- **GIVEN** `CODEX_LB_REQUEST_LOG_RETENTION_DAYS=90` and no dashboard override
- **WHEN** a client PUTs `requestLogRetentionOverrideDays: 90`
- **THEN** the override MUST be stored (the effective value stays 90 but no longer tracks the env alias)

#### Scenario: Present-null clears an override back to inherit

- **GIVEN** a stored dashboard override and `CODEX_LB_REQUEST_LOG_RETENTION_DAYS=90`
- **WHEN** a client PUTs `requestLogRetentionOverrideDays: null`
- **THEN** the stored value MUST return to `NULL = inherit` and the effective value MUST fall back to 90

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

### Requirement: Request-log pruning never deletes unfolded rows

Request-log pruning MUST run only while the fold is current (watermark within two fold lags of now) and MUST delete only rows with `requested_at` older than the retention cutoff AND at least one fold lag below the watermark, so concurrent summary readers holding a slightly older watermark can never lose rows from a just-folded window. When no rollup watermark exists, or the fold is catching up (initial backfill, stalled scheduler), request-log pruning MUST be skipped.

#### Scenario: Unfolded rows survive pruning

- **GIVEN** a request-log row older than the retention cutoff whose `requested_at` is above the fold watermark
- **WHEN** the retention job runs
- **THEN** the row MUST NOT be deleted

#### Scenario: Stalled fold suspends pruning

- **GIVEN** a fold watermark older than two fold lags
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted

#### Scenario: Lifetime totals are unchanged by pruning

- **GIVEN** folded request-log rows older than the retention cutoff
- **WHEN** the retention job deletes them and account usage summaries are read afterwards
- **THEN** per-account lifetime totals MUST equal their pre-pruning values

#### Scenario: Pruning is skipped before the first fold

- **GIVEN** no `account_usage_rollup_state` row exists
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted

### Requirement: Usage-history pruning preserves each identity's latest row

Usage-history pruning MUST delete only rows older than the retention cutoff and MUST always retain each identity's latest row per `(account_id, coalesce(window,'primary'))` in `usage_history` and per `(account_id, quota_key, window)` in `additional_usage_history`, regardless of age. "Latest" MUST follow the readers' ordering — newest `recorded_at`, protecting every row tied at that timestamp — not insertion order, so backfilled out-of-chronology rows cannot displace the last-known sample. On SQLite, the bulk-history cache MUST be invalidated after pruning.

#### Scenario: Idle account keeps its last-known usage

- **GIVEN** an account whose only usage rows are older than the retention cutoff
- **WHEN** the retention job runs
- **THEN** the newest row per window for that account MUST remain
- **AND** older rows for the same window MUST be deleted

#### Scenario: Out-of-chronology inserts keep the true latest sample

- **GIVEN** an identity whose highest-id row carries an older `recorded_at` than an earlier-inserted row
- **WHEN** the retention job runs
- **THEN** the row with the newest `recorded_at` MUST remain

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

