## Why

The dashboard already exposes `Reset-confirmed warm-up / Min usage %`, but
reset-confirmed warm-up currently ignores that setting and warms every real
reset. That makes the control misleading and prevents operators from choosing
between warming every confirmed reset and warming only windows that were used
above a configured floor.

The desired default remains to warm every confirmed reset, including accounts
whose pre-reset usage was only a few percent. A zero threshold expresses that
behavior without making the setting redundant: higher values remain available
for operators who want a stricter pre-reset usage gate.

## What Changes

- Make `limit_warmup_exhausted_threshold_percent` gate reset-confirmed warm-up
  candidates using `before.used_percent >= threshold`.
- Change the default from `99.0` to `0.0`, so every confirmed reset remains
  eligible by default.
- Accept `0` in backend and frontend validation and document its all-resets
  meaning in the dashboard.
- Apply the same threshold to the paid-to-Free monthly transition candidate;
  with a zero threshold, a missing pre-refresh sample remains eligible, while a
  positive threshold requires the sample.
- Activate the nullable compatibility column staged by the prerequisite PR,
  initialize every existing row to `0.0`, and make it non-null with a `0.0`
  server default.
- Keep the legacy column mapped internally for schema compatibility, but remove
  sentinels, provenance heuristics, triggers, dual writes, and legacy-column
  synchronization from the runtime and migration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Make the pre-reset warm-up threshold configurable
  again, with `0%` as the default all-resets behavior.
- `frontend-architecture`: Make the dashboard control accept and explain the
  zero-percent all-resets value.
- `database-migrations`: Record the active threshold column, legacy-column
  compatibility, historical-default mapping, and downgrade behavior.

## Impact

- Affected code: limit warm-up candidate selection, dashboard settings model,
  settings defaults/validation, frontend schema/control/locales, and the
  activation Alembic migration that depends on the compatibility stage.
- Affected tests: limit warm-up unit tests, settings API/schema tests, frontend
  component/schema tests, and migration assertions.
- No new endpoint, environment variable, dependency, or runtime worker is
  introduced.
