## Why

Request-scoped `internal_unanchored_parallel` HTTP bridge lanes retain their
account stream lease after a successful terminal response. Coding-agent
traffic can create these lanes faster than idle eviction closes them, so a
small number of sequential turns can exhaust the account stream cap even when
no fork has active work.

## What Changes

- Retire ordinary request-scoped unanchored fork lanes after their successful
  terminal response when no request, admission waiter, or handoff reservation
  remains.
- Close the whole lane through the existing bounded cleanup path instead of
  releasing its lease while leaving a reusable upstream socket uncounted.
- Preserve durable continuation aliases so later anchored traffic remains
  owner- and account-bound.
- Keep the live fork as a local continuity fallback when durable alias
  persistence is unavailable.
- Keep canonical session, prompt-cache, and account-neutral recovery lanes on
  their existing lifecycle.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: completed request-scoped fork lanes are retired
  without weakening durable continuity.
- `proxy-admission-control`: completed fork work promptly returns account-local
  stream capacity.

## Impact

- Code: `app/modules/proxy/_service/{support,http_bridge/{session_registry,upstream_events,request_submit}}.py`
- Tests: HTTP bridge lifecycle, concurrency, and external Responses bridge
  coverage
- Specs: `sticky-session-operations`, `proxy-admission-control`
