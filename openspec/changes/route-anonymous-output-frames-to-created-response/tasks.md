# Tasks

## 1. Frame ownership

- [x] 1.1 `_match_websocket_request_state_for_anonymous_event(event_type=...)`: for an anonymous `response.*` output frame (not `response.failed` / `response.incomplete`) return the single pending request whose `response_id` is already set, visible or draining, before the draining and unresolved-visible preferences; leave `error`, `response.failed`, `response.incomplete` and vendor-telemetry frames and every other branch unchanged.
- [x] 1.2 HTTP bridge reader (`_process_http_bridge_upstream_text`) and direct WebSocket reader (`_process_upstream_websocket_text`) pass the frame's `event_type` at the anonymous-match call.

## 2. Verification

- [x] 2.1 `tests/unit/test_http_bridge_cancel_drain.py`: matcher cases for a visible created response beside a pipelined sibling, beside an unresolved draining sibling, a draining created response beside a visible sibling, two visible created responses with nothing else pending staying unmatched, and a `codex.rate_limits` telemetry frame keeping pre-created ownership; bridge-reader cases proving `response.output_item.added` and `response.output_text.delta` reach the created response's queue while an anonymous `error` still pops the waiting sibling.
- [x] 2.2 `tests/unit/test_proxy_utils.py`: the direct WebSocket reader accounts an anonymous text delta to the created response.
- [x] 2.3 Regression: `uv run pytest tests/unit/test_http_bridge_cancel_drain.py tests/unit/test_proxy_http_bridge.py tests/unit/test_proxy_utils.py tests/unit/test_http_bridge_relay_loop.py tests/unit/test_http_bridge_eventless_semantics.py tests/unit/test_http_bridge_forwarding.py tests/unit/test_http_bridge_safe_continuity.py tests/unit/test_proxy_websocket_client.py tests/unit/test_proxy_api_websocket_auth.py tests/unit/test_proxy_websocket_overflow.py tests/unit/test_native_websocket_routing_fixtures.py tests/simulation`.
- [x] 2.4 Guards: `make lint`, `uv run ty check`, `openspec validate --specs`, `openspec validate route-anonymous-output-frames-to-created-response --strict`.
