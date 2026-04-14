## Why

Responses requests forwarded over the upstream websocket can accumulate historical inline images, Playwright screenshots, and oversized tool outputs into one serialized `response.create` payload. When that payload crosses the upstream websocket message budget, upstream closes the session with `1009 (message too big)`, which surfaces as an opaque `stream_incomplete` failure and can trap clients in reconnect loops.

## What Changes

- Measure serialized outbound `response.create` size before sending it to the upstream websocket.
- Slim only the historical portion of `input` before the most recent user turn by replacing historical inline images and oversized historical tool outputs with omission notices.
- Fail deterministically before upstream connect/reuse when the payload still exceeds budget, returning `payload_too_large` on `input`.
- Persist oversized request dumps and structured metadata for guarded failures and upstream `1009` incidents so operators can inspect what made the payload too large.

## Impact

- Code: `app/modules/proxy/service.py`
- Tests: `tests/integration/test_http_responses_bridge.py`, `tests/integration/test_proxy_websocket_responses.py`, `tests/unit/test_proxy_utils.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`
