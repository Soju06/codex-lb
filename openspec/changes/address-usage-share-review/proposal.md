## Why

PR #2463 carries obsolete branch-local migration convergence and an independent invalidation-bus change. Incomplete telemetry also bypasses the auth cache, repeating pool-wide reads on every request.

## What Changes

- Integrate current main and retain its streamed usage-limit refresh behavior.
- Remove branch-local convergence, preserve landed migrations unchanged, and use a new noncolliding usage-share revision on main's head.
- Defer the generic retained-invalidation change; restore main's bus and upstream-route contracts.
- Publish allocation invalidation through the existing API-key namespace instead of adding API-key callbacks to account-routing observations.
- Reuse incomplete authentication snapshots for at most five seconds using the existing cache and version fence.
- Preserve the submitted fail-open policy pending maintainer approval; do not silently adopt the separate account-cap policy.
- Finalize prepared direct-WebSocket states when fixed-limit reservation rejects a turn, without changing the existing client error or retiring a reusable upstream.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: bound reuse of incomplete usage-share authentication snapshots. Allocation invalidation retains its observable contract; the namespace wiring change is implementation-only.
- `proxy-admission-control`: account-neutral finalization of direct-WebSocket reservation refusals during preparation.

## Impact

Migration graph and lifecycle tests, API-key authentication cache, account-pool invalidation, streaming refresh helper, OpenSpec. No new settings, dependencies, dashboard surfaces, or production operations. Feature approval and fail-open versus fail-closed remain merge blockers outside this mechanical follow-up.
