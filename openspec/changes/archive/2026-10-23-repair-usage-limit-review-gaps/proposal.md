## Why

Review of PR #1528 found historical branch databases that skip upstream schema changes, rejection paths that lose cleanup ownership, and reserve displays that disagree with enforcement. Repair these gaps before publishing the feature as ready.

## What Changes

- Reconcile recognized historical usage-limit schema/stamp combinations while retaining the linear migration graph and saved data.
- Complete websocket and bridge rejection cleanup despite settlement failure or cancellation.
- Align account window presentation with enforcement and distinguish weekly reserves from consumed primary quota.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: Specify schema-aware compatibility for historical usage-limit branch revisions.
- `account-routing`: Specify partial-failure rejection cleanup and consistent reserve presentation.

## Impact

Migration startup tooling, account summaries, persistent proxy rejection paths, dashboard quota calculations, and focused regression tests. No settings or dependencies are added.
