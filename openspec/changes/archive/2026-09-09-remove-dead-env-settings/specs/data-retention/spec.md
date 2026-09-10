## ADDED Requirements

### Requirement: Retention is dashboard-configured, opt-in and validated

Retention MUST be disabled by default. Retention windows are resolved per
source from the dashboard runtime settings only: a non-NULL
`dashboard_settings.request_log_retention_days` /
`dashboard_settings.usage_history_retention_days` value MUST apply; when the
dashboard value is NULL (never configured) retention MUST be disabled. At
every layer the value `0` means disabled. The former env aliases
(`CODEX_LB_REQUEST_LOG_RETENTION_DAYS` / `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`)
MUST NOT influence the effective window; they are removed settings covered by
the removed-settings startup warning.

The dashboard settings API MUST expose, per retention window, the read-only
*effective* value (`requestLogRetentionDays` / `usageHistoryRetentionDays`)
alongside the nullable stored value
(`requestLogRetentionOverrideDays` / `usageHistoryRetentionOverrideDays`,
`null` = not configured). Updates MUST use only the override fields with
tri-state semantics: a field absent from the payload leaves the stored value
unchanged; a field present with `null` MUST clear the stored value back to
NULL; a field present with a value MUST store it. Because stored values
round-trip verbatim (null in, null out), a full GET-then-PUT save echoing the
override fields unchanged MUST NOT alter the stored values.

The dashboard settings API MUST accept `0` (disabled) or values at or above
their safety floors (30 days for request logs, 45 days for usage history) up
to 3650; configurations between 1 and the floor MUST be rejected with a
validation error.

#### Scenario: Default configuration deletes nothing

- **GIVEN** neither retention setting has been configured in the dashboard
- **WHEN** the retention job runs
- **THEN** no rows are deleted from `request_logs`, `usage_history`, or `additional_usage_history`

#### Scenario: Unsafe dashboard retention values are rejected

- **WHEN** a dashboard settings update carries `requestLogRetentionOverrideDays=7` or `usageHistoryRetentionOverrideDays=10`
- **THEN** the API MUST reject the update with a validation error (the internal validator message names the violated floor)
- **AND** the stored settings MUST remain unchanged

#### Scenario: Full-save echoes round-trip unchanged

- **GIVEN** no dashboard value is stored
- **WHEN** a client performs a full GET-then-PUT save echoing `requestLogRetentionOverrideDays: null` back
- **THEN** the stored value remains `NULL` and the effective value stays `0` (disabled)

#### Scenario: Present-null clears a stored value back to disabled

- **GIVEN** a stored dashboard value of `90`
- **WHEN** a client PUTs `requestLogRetentionOverrideDays: null`
- **THEN** the stored value MUST return to `NULL` and the effective value MUST fall back to `0` (disabled)

#### Scenario: Dashboard zero disables retention

- **GIVEN** a dashboard `usage_history_retention_days` value of `0`
- **WHEN** the retention job runs
- **THEN** no `usage_history` rows are deleted

#### Scenario: Removed env alias has no effect

- **GIVEN** a NULL dashboard value and `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS=45` still set in the environment
- **WHEN** the application starts and the retention job runs
- **THEN** startup logs the removed-settings warning naming `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`
- **AND** the effective usage-history retention is `0` and no rows are deleted

## MODIFIED Requirements

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
- **GIVEN** both dashboard retention windows resolve to 0 (stored 0 or NULL)
- **WHEN** the scheduler ticks
- **THEN** no retention pass runs

## REMOVED Requirements

### Requirement: Retention is opt-in and validated

**Reason**: The requirement encoded a three-layer precedence (dashboard value, deprecated env alias, disabled). The env aliases `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` / `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS` were one-release deprecated aliases (v1.21.x) and have been removed after v1.22-v1.24 shipped; the env-specific scenarios can no longer hold.

**Migration**: Replaced by "Retention is dashboard-configured, opt-in and validated" below. Operators still setting the env aliases see the removed-settings startup warning and set the window once from Settings -> Advanced -> Data retention.
