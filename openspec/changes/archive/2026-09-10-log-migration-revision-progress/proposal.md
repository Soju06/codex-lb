## Why

Issue #1470 reports startup migrations that look hung. Operators need revision identity and elapsed execution time before deciding whether to interrupt deployment.

## What Changes

- Log revision start, execution completion, and failure without database contents.
- Preserve Alembic ordering, transactions, and exception propagation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: online revision execution progress.

## Impact

Alembic online environment and migration CLI logging. Partial coverage of #1470. Background data phases, row counts, batch percentages, recovery guidance, and #1471 benchmark policy remain separate.
