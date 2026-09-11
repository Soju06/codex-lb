## 1. Drain-time WebSocket denial

- [x] 1.1 Replace the pre-accept `websocket.close(1013)` in
  `app/core/middleware/inflight.py` with `deny_websocket_with_http_response`
  (`503`, `merge_retry_after_headers()`), keeping the in-flight decrement before
  the denial and never invoking the route handler.
- [x] 1.2 Select the denial body by path: `local_unavailable_error("Server is
  draining")` for proxy paths, `{"detail": "Server is draining"}` otherwise; share
  the message constant with the HTTP drain response.

## 2. Regression coverage

- [x] 2.1 `tests/unit/test_graceful_shutdown.py`: assert the
  `websocket.http.response.start` / `websocket.http.response.body` sequence,
  status `503`, `retry-after: 5`, the `proxy_unavailable` envelope for
  `/v1/responses`, the generic `detail` body for a non-proxy path, no route
  invocation, and an unchanged in-flight count.
- [x] 2.2 `tests/integration/test_proxy_websocket_responses.py`: the late
  admission step of the route-drain test expects `WebSocketDenialResponse` with
  status `503`, `Retry-After: 5`, and `error.code = "proxy_unavailable"` while
  the admitted turn keeps the in-flight count at one.

## 3. Verification

- [x] 3.1 `uv run pytest tests/unit/test_graceful_shutdown.py
  tests/unit/test_bulkhead.py tests/unit/test_backpressure.py` and
  `uv run pytest tests/integration -k "drain or graceful or shutdown"`.
- [x] 3.2 `ruff check`, `ruff format --check`, `ty check`, and the proxy
  architecture / cancellation-safety / timing-seam / settings-tier scripts.
- [x] 3.3 `openspec validate deny-drain-websocket-upgrades-with-503 --strict`
  and `openspec validate --specs`.
