## Why

Main now ends at the dashboard audit actor revision while the published rejection/guest merge remains a second head. Public upgrade head cannot choose a target.

## What Changes

Append a no-op merge joining the actual two heads. Preserve every published revision and prove populated upgrades and merge-only roundtrips on SQLite and PostgreSQL.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: converge dashboard-user and rejection histories without rewriting history or altering policy.

## Impact

One migration revision, focused migration regression tests, PostgreSQL test selection, and migration specs. No live or authorization-policy change.
