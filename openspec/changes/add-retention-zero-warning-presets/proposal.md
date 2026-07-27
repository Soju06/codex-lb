## Why

An effective request-log retention value of `0` disables pruning but currently
appears only as an inherited-value hint. Operators need a neutral explanation
of the storage implications and quick shortcuts for choosing common bounded
policies without the dashboard silently changing an existing policy.

## What Changes

- Show neutral informational text when effective request-log retention is
  disabled.
- Offer 30-day and 90-day request-log retention presets.
- Keep presets non-destructive: choosing one only edits the local form value,
  and the existing explicit save action remains required to change policy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `data-retention`: Define the disabled-state information and explicit-save
  behavior for request-log retention presets.

## Impact

- Affected code: retention settings component, supported locale strings, and
  focused component tests.
- No API, schema, database, scheduler, deployment, or default-policy changes.
