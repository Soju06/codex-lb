# Retry confirmed account-proxy connect failures

## Why

An account-bound upstream proxy can remain administratively active after its
listener stops accepting connections. Responses requests selected onto that
account then fail before upstream sees the request, but the proxy currently
turns the transport failure into a terminal stream event or a bridge startup
error. Client retries may select the same account again because a confirmed
dead route only accrues generic single errors (#1314).

## What Changes

- Reuse the existing sanitized dispatch provenance
  (`retryable_same_contract` + `failure_phase == "connect"`) as the single
  predicate that authorizes cross-account replay, excluding host-wide
  network loss and TLS verification failures.
- When the transport proves the connection to the selected proxy failed
  before request dispatch, try another endpoint in the same proxy pool even
  for a non-idempotent POST, then another eligible account for movable
  Responses requests across raw HTTP/SSE, native Responses WebSocket, and
  HTTP bridge session startup.
- Release the failed account's stream lease before recording bounded
  transient account backoff (`record_error_backoff` jumps directly to the
  existing 30s floor, capped at 300s) so independent requests stop
  rediscovering the dead route.
- Preserve the original sanitized upstream-unavailable failure when no
  replacement account exists. Ambiguous failures and hard continuity, file,
  or required-account ownership remain non-replayable and fail closed.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `upstream-proxy-routing`

## Impact

No API surface, schema, or dependency changes. Selection health-state gains
a minimum-error-count floor used only by the confirmed dead-route path; the
generic transient-error accounting, process network recovery, and
downstream-visible fail-closed semantics are unchanged.
