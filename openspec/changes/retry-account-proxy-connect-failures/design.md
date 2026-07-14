# Design

## Transport provenance

`CodexTransportError` will carry a typed dispatch state and the sanitized
transport exception class. The only replay-safe state is `not_dispatched`,
which is assigned when the client library explicitly reports failure to
connect to the configured HTTP or SOCKS proxy. Generic connector errors,
timeouts, response-body failures, websocket receive failures, and unknown
exceptions remain `unknown`.

The state is copied onto `ProxyResponseError` when websocket/bridge startup or
HTTP stream startup crosses the core-client boundary. No proxy URL,
credentials, or raw exception text is retained or exposed.

## Retry order

For an HTTP POST, a confirmed `not_dispatched` failure may try the next endpoint
in the already-resolved proxy pool. This is safe despite the method being
non-idempotent because no request reached upstream. Ambiguous POST failures
retain the existing no-fallback rule.

If every same-pool endpoint fails before dispatch, raw HTTP/SSE, native
Responses WebSocket, and the HTTP responses bridge exclude the selected
account and use the existing bounded account-selection loop. The original
sanitized 502 is retained and returned if no replacement exists.

## Ownership boundary

Only movable requests can cross accounts. A client or proxy continuation that
requires its owner, an account-scoped uploaded file, a forced single-account
route, or another hard preferred-account contract fails closed on the original
account. Soft prompt-cache and process-session affinity may move.

## Account backoff and resource ordering

A confirmed dead account route is stronger evidence than a generic transient
stream error. It raises the account to the existing transient error-backoff
floor immediately: 30 seconds at the floor, exponentially bounded by the
existing 300-second cap. It does not pause, deactivate, rate-limit, or
quota-penalize the account.

Per-account response-create and stream leases are released before recording
the backoff. The downstream API-key reservation is request-scoped rather than
account-scoped, so an internal pre-dispatch failover keeps that single
reservation alive instead of releasing and racing to reacquire it. The normal
terminal finalizer settles or releases it exactly once after the replacement
attempt or final failure.

## Non-goals

- Do not replay ambiguous failures after proxy acceptance, header wait, or
  response-body processing.
- Do not add endpoint-health persistence or change proxy-pool membership.
- Do not broaden generic `upstream_unavailable` retry classification.
