# Design

## Capacity seed

`SettingsRepository.get_or_create` constructs the row with `None` for the
four nullable capacity columns. Every reader already handles `NULL`
(`SettingsService._effective_*`, `cap_partitioning`, `proxy/service.py`,
`_service/support.py`), because rows migrated from before the columns
existed were already `NULL`; this change only removes the seed that hid that
path on fresh installs.

No data migration: the environment value a row was seeded from is not
recoverable at migration time, and clearing every row whose value happens to
equal today's default would also clear intentional overrides. Existing rows
therefore keep their override and the UI shows it as a dashboard value,
which is what it has been all along.

## Telemetry precedence

`resolve_consent(telemetry_enabled, persisted_state)`:

1. `persisted_state in {enabled, disabled}` -> `source=persisted`, active per
   the decision. The environment is not consulted.
2. `undecided` and `telemetry_enabled is not None` -> `source=env`, state and
   activity per the variable. The dialog stays suppressed (there is a
   deciding value) and nothing is written to the row.
3. `undecided` and no variable -> `source=default`, active.

The environment value is intentionally not persisted as a decision when
read: persisting would make an operator's later `unset` ineffective and
would write to the database on a read path.

`PUT /api/settings/telemetry` is unchanged: it stores the decision and
resolves again. Because a stored decision now wins, the response carries
`source=persisted`, and the existing "previous.active and not consent.active
-> send one opt-out notice" rule applies to the env-active -> dashboard-
disabled transition too.

## Frontend

`TelemetrySettings` disables the switch only for read-only sessions, pending
mutation, or missing data. `source === "env"` renders the fallback notice.
`TelemetryConsentDialog` keeps its `source !== "env"` gate: while the
environment decides there is nothing to ask.
