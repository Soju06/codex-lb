## Why

The dashboard OAuth hook polls status and then completes using whatever flow credentials sit in the current React state after each await. If the operator resets or restarts OAuth while an in-flight poll is still waiting, that stale poll can complete, error, or invalidate caches for the new flow. The API already keys status by flow ID; the client must stop applying a previous generation's result to the current generation.

## What Changes

- Fence dashboard OAuth polling with a monotonic generation that reset and restart invalidate.
- Capture the in-flight flow ID and completion credentials before awaits, and ignore stale status or completion results after generation or identity no longer match.
- Keep the current-flow success, error, and pending paths unchanged when no reset or restart occurs.
- Add a deterministic deferred-promise regression that starts flow A, resets and starts flow B, then resolves A without mutating B.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-identity`: a reset or restarted dashboard OAuth flow MUST ignore stale poll completions from a previous generation so the current flow's identity, status, and account-cache invalidation stay isolated.

## Impact

- Frontend only: `frontend/src/features/accounts/hooks/use-oauth.ts` and `use-oauth.test.ts`.
- No backend, API, schema, or visible OAuth dialog contract changes.
- No new settings, dependencies, or dashboard navigation.
