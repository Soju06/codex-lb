## Why

CI validates canonical capabilities but lets malformed active change deltas pass. Issue #2032 asks for strict validation of touched changes without blocking on unrelated legacy deltas.

## What Changes

- Validate surviving active change folders selected from the event's Git diff, using strict OpenSpec validation.
- Preserve canonical validation and the required OpenSpec job.
- Cover PR merge bases, push and merge queue ranges, deletions, renames and unrelated invalid changes through the CI script interface.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-automation`: Require strict validation of changed active OpenSpec folders.

## Impact

Only the OpenSpec CI job, its validation script and regression tests change. No runtime configuration or database changes.
