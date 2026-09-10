## Why

While the process drains, `InFlightMiddleware` rejected a new WebSocket
connection by sending `websocket.close` (code 1013) before the handshake was
accepted. ASGI servers surface a pre-accept close as a bare HTTP `403`
upgrade response: Uvicorn logs `connection rejected (403 Forbidden)`, and
OpenAI-SDK-style clients, which retry only `408`/`409`/`429`/`5xx`, treat the
upgrade failure as a terminal access error. A single-replica restart therefore
produced dozens of apparent auth failures in the access log and non-retried
client errors, even though the condition is transient by definition.

The overload bulkhead already made the opposite choice for the same surface:
`proxy-admission-control` requires that a locally rejected websocket handshake
receive an HTTP denial response with the real status instead of a pre-accept
close frame. Drain and overload should reject upgrades the same way.

## What Changes

- During drain, a new WebSocket upgrade is denied with an HTTP denial response
  (`websocket.http.response.start` / `websocket.http.response.body`) of status
  `503` carrying `Retry-After: 5`, instead of a pre-accept `websocket.close`.
- The denial body follows the path: proxy paths (`/v1/...`,
  `/backend-api/...`) receive the OpenAI-style local-unavailable envelope
  (`error.code = "proxy_unavailable"`, `error.type = "server_error"`,
  `error.message = "Server is draining"`); every other WebSocket path receives
  `{"detail": "Server is draining"}`.
- The existing admission guarantees are unchanged: the route handler is not
  invoked and the rejected connection does not increase the in-flight count.
- The HTTP drain rejection is unchanged; it only shares the message constant.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `graceful-shutdown`: the "Graceful drain closes WebSocket admission"
  requirement now prescribes the observable rejection contract (HTTP denial
  response, `503`, `Retry-After`, per-path error body) in addition to the
  existing no-route-invocation and no-count-growth guarantees.

## Impact

- `app/core/middleware/inflight.py` reuses `deny_websocket_with_http_response`,
  `local_unavailable_error`, `is_proxy_path`, and `merge_retry_after_headers`
  from `app/core/resilience/overload.py`; no new helper, setting, dependency,
  migration, or route.
- Compatibility: the externally visible drain rejection for WebSocket upgrades
  changes from an opaque `403` upgrade failure to a retryable `503` with
  `Retry-After`. Clients that already retried the `403` keep retrying; clients
  that treated it as terminal now retry. Access logs show `503` instead of
  `403 Forbidden` for late upgrades during a restart.
- Tests: `tests/unit/test_graceful_shutdown.py` (proxy and generic paths) and
  the route-drain integration test in
  `tests/integration/test_proxy_websocket_responses.py` assert the new contract.
