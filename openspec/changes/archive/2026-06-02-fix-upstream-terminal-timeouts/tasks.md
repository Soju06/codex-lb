## 1. Implementation

- [x] 1.1 Preserve `stream_idle_timeout` classification when `stream_idle_timeout_seconds` equals `proxy_request_budget_seconds`, the upstream response body has had no activity for a full idle window, and scheduler jitter wakes after the shared deadline.
- [x] 1.2 Preserve `upstream_request_timeout` classification when the request budget is strictly shorter than the idle timeout, when remaining request budget is shorter than a fresh idle window, when a generic total timeout fires before an upstream response has started, or when a generic total timeout follows recent body activity.
- [x] 1.3 Avoid fail-all pending behavior for equal idle/budget websocket timeout ties so younger pending requests keep their own budget.
- [x] 1.4 Emit HTTP bridge downstream SSE liveness frames while waiting for upstream queue events, delay the first generated keepalive until after the startup-error probe window, and mark emitted keepalives as streamed bytes for later response-failed handling.
- [x] 1.5 Preserve SSE comment keepalives through public `/v1/responses` stream normalization.

## 2. Tests

- [x] 2.1 Unit: direct HTTP/SSE stream maps equal total/idle deadline ties after response startup and no recent body activity to `stream_idle_timeout`, keeps pre-response total timeout as `upstream_request_timeout`, preserves shorter budgets as `upstream_request_timeout`, and keeps post-chunk total timeouts classified by request budget when recent body activity exists.
- [x] 2.2 Unit: websocket timeout selection preserves idle classification without setting `fail_all_pending` only for full-window equal idle/budget ties, and keeps shorter remaining-budget waits classified as `upstream_request_timeout`.
- [x] 2.3 Unit: expired websocket pending requests are failed without removing younger pending requests.
- [x] 2.4 Unit: HTTP owner-forward receive timeout preserves idle classification on full-window equal idle/budget ties and preserves budget classification when remaining budget is shorter than a fresh idle window.
- [x] 2.5 Unit: HTTP bridge event streams emit liveness frames, delay the first generated keepalive past the startup probe window, mark generated keepalives as already-yielded stream bytes for later response-failed handling, and public stream normalization preserves comment keepalives.

## 3. Spec Delta

- [x] 3.1 Add `responses-api-compat` requirements for timeout tie classification, multiplexed pending preservation, and HTTP bridge SSE liveness frames.
- [x] 3.2 Validate the OpenSpec change with `openspec validate fix-upstream-terminal-timeouts --strict`.
