## Why

`claude-balances` can read codex-lb fleet summary data, but its Codex pressure
and continuity cards currently degrade because codex-lb does not expose a
read-only observability feed through the fleet API-key surface.

## What Changes

- Add API-key-authenticated `GET /api/fleet/observability`.
- Return 30-minute and 2-hour request pressure windows from request logs.
- Return sticky-session continuity counts grouped by account and kind.
- Reuse fleet summary account scoping and usage-visibility policy.
- Exclude sensitive request/session/sticky identifiers and raw error payloads.

## Impact

- Trusted local fleet consumers can render Codex pressure and sticky-session
  continuity without dashboard credentials.
- No database migration is required; the endpoint reads existing request-log and
  sticky-session tables through current indexes.
- Keys without `account_pool_usage` visibility, or keys blocked by the global
  quota privacy setting, receive an empty non-sensitive observability payload.
