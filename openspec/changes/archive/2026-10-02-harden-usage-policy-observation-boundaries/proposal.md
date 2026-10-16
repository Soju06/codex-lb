## Why

Warmup authorization currently copies a fresh policy into a session-owned account, allowing subsequent settlement to overwrite a newer operator edit. Quota-planner dispatch also awaits another settings read after final authorization, and cancellation after a telemetry commit can skip routing invalidation.

## What Changes

- Keep fresh warmup account projections transient so read authorization cannot write policy or status.
- Use the already-resolved quota-planner dashboard snapshot at dispatch instead of awaiting another settings read.
- Invalidate routing after a committed standard observation even if the post-commit policy read is cancelled.
- Remove unused policy exception and cached-input sharing options introduced by this feature.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Preserve concurrent policy writes through warmup settlement and cancellation after usage commits.

## Impact

Usage refresh, reset warmup, quota-planner warmup, and their existing integration/unit tests. No schema, dependency, configuration, or public API changes.
