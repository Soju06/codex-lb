## Why

The PR #2093 Responses-route regression found that the generic pre-visible
error handler can record permanent revoked-token health before forced refresh
and while the API-key usage reservation is still open.

## What Changes

- Send initial streaming HTTP 401 failures directly to the existing forced-refresh handler.
- Preserve same-account refresh retry, account exclusion, and settlement-before-health ordering.
- Add real Responses-route and WebSocket-relay revoked-token regressions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: verify revoked access-token refresh and settlement ordering.

## Impact

Streaming retry orchestration, proxy route tests, and account-routing requirements.
No new settings, dependencies, schema, or public error format.
