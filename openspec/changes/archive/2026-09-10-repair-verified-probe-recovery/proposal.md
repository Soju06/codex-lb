## Why

PR #2330 fails supported schema bootstrap and leaves recovered holds in a replica cache when a transient error arrives before selection. CI also exposes stale probe test setup.

## What Changes

- Reconcile recovered generations before direct runtime failure updates.
- Make the unmerged rejection-generation migration tolerate already present columns during schema bootstrap.
- Repair probe test doubles and disposable database setup without changing their behavioral assertions.

## Capabilities

### New Capabilities

### Modified Capabilities

- `account-routing`: cover a transient error arriving after recovery but before replica selection.
- `database-migrations`: preserve existing rejection evidence during bootstrap with present columns.

## Impact

Load-balancer state reconciliation, the unmerged rejection-generation migration, and affected route, startup and migration tests. No new settings or live changes. Refs #2327.
