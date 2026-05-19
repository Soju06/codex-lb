## Why
Device Code OAuth can stay pending forever when the client misses or fails the compatibility `/api/oauth/complete` request after `/api/oauth/start` returns a `deviceAuthId` and `userCode`. The backend has enough state to poll immediately, so requiring a second client request makes account setup fragile behind remote dashboards, proxies, or flaky browser sessions.

## What Changes
- Start the Device Code token polling task as soon as `/api/oauth/start` initializes a device flow.
- Keep `/api/oauth/complete` as an idempotent compatibility endpoint that starts polling only if no active poll task exists.
- Cover the robust start-only device flow with an integration regression test.

## Impact
- Affects backend OAuth device-flow state management.
- No request or response schema changes are required.
- Fixes https://github.com/Soju06/codex-lb/issues/110.
