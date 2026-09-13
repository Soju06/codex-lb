# data-retention Specification

## Purpose

Define opt-in retention of request logs and usage history — dashboard-first configuration, safety floors, and pruning invariants — so aged rows can be deleted without losing lifetime totals, latest-known usage, or unfolded data.
## Requirements
### Requirement: Request-log pruning never deletes unfolded rows

Request-log pruning MUST gate on every usage-rollup watermark — the lifetime `folded_through`, the time-axis `hourly_folded_through`, the conversation satellite's `conversation_folded_through`, and the report history's `reports_folded_through` — combined as their minimum. Pruning MUST run only while the combined fold is current (the minimum watermark within two fold lags of now) and MUST delete only rows with `requested_at` older than the retention cutoff AND at least one fold lag below the minimum watermark, so concurrent summary readers holding a slightly older watermark can never lose rows from a just-folded window and no rollup is ever robbed of raw it has not folded. When no rollup watermark exists, or any fold is catching up (initial backfill, stalled scheduler), request-log pruning MUST be skipped.

#### Scenario: Unfolded rows survive pruning

- **GIVEN** a request-log row older than the retention cutoff whose `requested_at` is above any fold watermark
- **WHEN** the retention job runs
- **THEN** the row MUST NOT be deleted

#### Scenario: Stalled fold suspends pruning

- **GIVEN** any fold watermark older than two fold lags
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted

#### Scenario: Conversation backfill suspends pruning

- **GIVEN** a deployment upgraded with existing history, where the lifetime and hourly watermarks are current but `conversation_folded_through` is still at or near the epoch
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted until the conversation backfill watermark becomes current

#### Scenario: Lifetime totals are unchanged by pruning

- **GIVEN** folded request-log rows older than the retention cutoff
- **WHEN** the retention job deletes them and account usage summaries are read afterwards
- **THEN** per-account lifetime totals MUST equal their pre-pruning values

#### Scenario: Conversation statistics are unchanged by pruning

- **GIVEN** request-log rows folded into the conversation presence satellite and older than the retention cutoff
- **WHEN** the retention job deletes them
- **THEN** the switched distinct-conversation reads over the pruned period MUST equal their pre-pruning values

#### Scenario: Pruning is skipped before the first fold

- **GIVEN** no `account_usage_rollup_state` row exists
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted

#### Scenario: Report backfill gates pruning

- **GIVEN** the other rollup watermarks are current but `reports_folded_through` is still behind
- **WHEN** retention runs
- **THEN** it SHALL leave request logs intact until the report watermark becomes current
- **AND** report totals SHALL survive subsequent pruning of the folded period

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
- **GIVEN** both dashboard retention windows resolve to 0 (stored 0 or NULL)
- **WHEN** the scheduler ticks
- **THEN** no retention pass runs

### Requirement: Disabled request-log pruning is explained and presets are non-destructive

When the effective request-log retention value is `0`, the Settings data retention card SHALL show neutral informational text that request-log pruning is disabled, logs are retained indefinitely, and storage will grow over time, and SHALL offer 30-day and 90-day request-log retention presets. The informational text MUST NOT characterize disabled pruning as unsafe or direct the operator to change it. Activating a preset MUST update only the local request-log retention form value and MUST NOT persist any setting until the operator activates the existing explicit save action. Rendering the information and presets MUST NOT change the stored override or any other retention policy.

#### Scenario: Effective disabled state shows information and presets

- **GIVEN** effective request-log retention is `0`
- **WHEN** an operator views the data retention card
- **THEN** the card explains neutrally that request-log pruning is disabled and
  logs are retained indefinitely
- **AND** the text notes that storage will grow over time without directing the
  operator to change the policy
- **AND** the card offers 30-day and 90-day request-log retention presets
- **AND** no settings update is submitted

#### Scenario: Preset selection requires explicit save

- **GIVEN** effective request-log retention is `0`
- **WHEN** an operator activates the 30-day or 90-day preset
- **THEN** the request-log retention form value changes to the selected number
- **AND** no settings update is submitted until the operator activates save
- **AND** usage-history retention remains unchanged

#### Scenario: Enabled effective policy does not show disabled-state information

- **GIVEN** effective request-log retention is greater than `0`
- **WHEN** an operator views the data retention card
- **THEN** the disabled-state information and presets are not shown

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

### Requirement: The bridge operation spool window is dashboard-managed with a derived floor

The durable HTTP bridge operation spool holds the raw request payload of a
bridged turn and the response events recovery replays, so its retention window
is prompt-data retention and MUST be manageable from the Settings data
retention card alongside the request-log and usage-history windows. Unlike
those two, it MUST NOT be disable-able: the window is a positive duration in
seconds, resolved as code default, then the deprecated environment alias, then
a non-NULL `dashboard_settings` column of the same name. The settings API MUST
expose the effective value together with its provenance and MUST accept the
tri-state update used by the other inheritable settings: a field absent from
the payload leaves the stored value unchanged, a field present with `null`
clears it back to inheriting, and a field present with a value stores it.

The API MUST reject an update whose effective window falls below the longest
window in which a spooled operation can still be read, and the rejection MUST
name the binding term. That floor MUST be derived from the reuse windows in
force — not a fixed number — because operators can raise some of them, and the
same check MUST therefore also reject raising a reuse window past a stored
spool window.

A deployment can already be below the floor without any update having passed
that check, because the environment alias alone decides the window while the
column is NULL. That state MUST NOT make the settings surface read-only, and it
MUST NOT suspend the check either: while below the floor an update MUST be
accepted only when it leaves the effective window no shorter and the floor no
higher than it found them, and MUST be rejected when it would deepen the
violation. The API MUST report the current floor so the dashboard can state it
and mirror the check before submitting.

#### Scenario: Card manages the spool window with its provenance

- **GIVEN** no dashboard value has been stored
- **WHEN** an operator views the data retention card
- **THEN** the spool window is shown as inherited, stating that the spool holds
  raw request payloads and that a shorter window deletes them sooner
- **AND** the card states the floor the API would enforce

#### Scenario: A window below the replay floor is rejected

- **WHEN** a dashboard settings update would leave the effective spool window
  below the derived floor
- **THEN** the API rejects the update with a validation error naming the
  binding term
- **AND** the stored settings remain unchanged

#### Scenario: Raising a reuse window past the stored spool window is rejected

- **GIVEN** a stored spool window equal to the current floor
- **WHEN** an update raises one of the reuse windows the floor is derived from
  without also raising the spool window
- **THEN** the API rejects the update
- **AND** the same update is accepted when it raises both together

#### Scenario: A configuration already below the floor may only improve

- **GIVEN** an effective spool window below the floor that no update stored
- **WHEN** an update changes an unrelated setting, or raises the window toward
  the floor
- **THEN** the API accepts it
- **AND** an update that lowers the window further, or raises one of the terms
  the floor is derived from, is rejected

#### Scenario: Present-null returns the spool window to inheriting

- **GIVEN** a stored dashboard spool window
- **WHEN** a client PUTs `null` for it
- **THEN** the stored value returns to NULL and the effective value falls back
  to the environment alias, or the code default when the alias is unset

