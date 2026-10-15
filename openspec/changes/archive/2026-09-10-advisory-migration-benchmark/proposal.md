## Why

#1471 reports a request-log backfill taking about ten minutes over 3.2 million rows despite passing tiny-database checks. Developers need reproducible measurements before deciding whether a migration needs further work.

## What Changes

- Add an explicitly invoked PostgreSQL benchmark with deterministic synthetic accounts and request logs.
- Measure selected Alembic revisions through the existing migration CLI and emit JSON and Markdown with provenance and failure state.
- Keep durations advisory. This is a partial contribution to #1471; CI scheduling, thresholds and exception policy remain outside scope.

## Capabilities

### New Capabilities

- `migration-benchmark`: Disposable fixture creation and advisory revision measurements.

### Modified Capabilities

None. The runtime migration mechanism remains unchanged.

## Impact

Developer scripts, subprocess integration tests and OpenSpec documentation. No runtime configuration, dependencies, frontend or CI gate changes.
