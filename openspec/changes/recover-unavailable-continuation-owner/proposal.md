# Recover unavailable previous-response owners via durable fresh replay

## Why

A `previous_response_id` continuation is hard-owner-bound: only the account
that produced the original response can safely resolve it. When that owner
account is later unavailable (quota exhausted, paused, deactivated), the
proxy already has a narrow escape hatch — if it can prove the client's full
resend body is an exact, verified replay of the input the owner actually
completed, it strips the anchor and continues fresh on any healthy account.
That proof today lives only in a process-local, LRU-bounded, in-memory index
(`_websocket_continuity_index`). It is empty after a process restart, an
eviction, or on a replica other than the one that completed the original
response, so a perfectly resumable conversation instead fails closed with
`previous_response_owner_unavailable` ("Hard affinity owner account is
unavailable") even when `stickyThreadsEnabled` is off — sticky affinity is a
different, unrelated mechanism from this hard-ownership check.

Separately, the HTTP responses bridge (`http_responses_session_bridge_enabled`,
on by default) has no recovery at all for this case when the client itself
supplied `previous_response_id`. Its only existing fresh-replay recovery is
scoped to a different, proxy-injected reattach scenario, so a client-anchored
continuation whose bridge owner becomes unavailable always fails closed today,
regardless of the in-memory cache.

## What Changes

- Persist the same input-item-count/fingerprint pair already computed for
  in-memory continuity (`RequestLog.input_item_count`,
  `input_full_fingerprint`) so a completed response's replay-verification data
  survives restarts and is visible to every replica, not just the one that
  streamed it.
- When resolving a `previous_response_id` owner from durable storage
  (`_resolve_websocket_previous_response_owner`, shared by the direct retry
  path, the HTTP bridge, and native WebSocket), warm the in-memory continuity
  cache from that same row instead of leaving it empty. This never overwrites
  an existing entry, so a completion this process already observed directly
  is never clobbered by an older durable row.
- In the direct HTTP/WebSocket retry path (`_stream_with_retry`), re-check the
  verified-fresh-replay condition after owner resolution so a cache warmed
  from durable storage can still unlock the existing (previously
  cache-only-gated) recovery branches later in the same request.
- In the HTTP responses bridge (`_stream_via_http_bridge`), add the missing
  recovery branch for a client-supplied `previous_response_id` whose owner
  turns out to be unavailable: attempt the same verified-fresh-replay check,
  and only strip the anchor and resend when it succeeds.
- None of this weakens the existing fail-closed guarantee: unless the durable
  fingerprint proves an exact match against the resend body, or the input is
  not a self-contained fresh replay (e.g. it references file uploads or
  dangling tool calls), the request still fails closed exactly as before.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`: a resolved-but-unavailable hard continuation owner
  MUST get a verified-fresh-replay recovery attempt sourced from durable
  storage, across the direct retry path and the HTTP bridge, before failing
  closed.

## Impact

- Code: `app/db/models.py`, one Alembic migration, request-log persistence
  (`app/modules/proxy/_service/request_log.py`, `streaming/mixin.py`,
  `websocket/mixin.py`), the shared owner-resolution helper
  (`websocket/mixin.py`), the direct retry state machine
  (`streaming/retry.py`), and the HTTP responses bridge
  (`http_bridge/streaming.py`).
- Tests: durable-fallback recovery for a cold continuity cache (direct path)
  and for a client-anchored HTTP-bridge continuation, plus existing repository
  and request-log-shape regressions updated for the new columns.
- API/schema: additive, nullable `request_logs` columns; no public shape
  change.
