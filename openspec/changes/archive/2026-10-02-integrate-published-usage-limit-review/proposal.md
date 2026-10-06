## Why

The reviewed local usage-limit branch and PR #1528's published branch have diverged. Publication must preserve the published per-window reserve controls and migrations together with the verified local authorization, telemetry, and upstream-integration fixes.

## What Changes

- Merge the published PR history without rebasing or replacing either side.
- Preserve scalar and per-window policies, reserve presentation, saved-policy updates, and published review fixes.
- Retain dispatch/preparation authorization, sticky admission fencing, unavailable telemetry, and canonical Responses errors from the local review.
- Join migration heads and verify upgrades from local and published schema histories with saved policies intact.
- Verify the combined implementation, sync specs, and push the existing PR branch without force.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Preserve per-window policy behavior across the reviewed authorization boundaries.
- `database-migrations`: Converge local and published usage-limit history without losing policy data.

## Impact

Account policy persistence/API/dashboard, quota evaluation, routing and bridge/WebSocket/warmup authorization, telemetry, migration topology, and regression coverage. No new dependency, service, or setting is required.
