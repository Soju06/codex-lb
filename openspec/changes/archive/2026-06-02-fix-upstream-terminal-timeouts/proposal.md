## Why

Long-running Codex Responses turns can hit two deadlines at nearly the same time: the upstream stream idle timeout and the overall proxy request budget. The defaults intentionally set both to 600 seconds. When the event loop wakes a few milliseconds after that shared deadline, the proxy was classifying the terminal condition as `upstream_request_timeout` in some paths even though the configured idle watchdog was the deadline operators expected to see.

The websocket and HTTP bridge paths add a second failure mode: they can multiplex multiple pending turns over one upstream session. Treating an equal idle/budget tie as a fail-all idle timeout can close younger sibling turns that still have their own request budget remaining. That produces misleading `HTTP bridge session closed before response.completed` failures on unrelated in-flight requests.

HTTP bridge responses also need liveness frames while a request is waiting on the upstream event queue; otherwise downstream SSE clients can disconnect before the terminal upstream frame arrives.

## What Changes

- Preserve `stream_idle_timeout` classification when the configured stream idle timeout equals the request budget and scheduler jitter wakes after the shared deadline.
- Keep true shorter request budgets classified as `upstream_request_timeout`.
- For multiplexed websocket / HTTP bridge pending queues, avoid fail-all behavior on equal idle/budget ties; expire only requests whose own budget elapsed so younger siblings continue waiting.
- Emit HTTP bridge downstream SSE liveness frames while a pending turn has no upstream event yet.
- Preserve SSE comment keepalives through the public `/v1/responses` stream normalizer.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: streaming Responses timeout classification and downstream liveness behavior.

## Impact

- **Code**: `app/core/clients/proxy.py`, `app/modules/proxy/api.py`, `app/modules/proxy/http_bridge_forwarding.py`, `app/modules/proxy/service.py`
- **Tests**: focused unit coverage in `tests/unit/test_proxy_utils.py`, `tests/unit/test_http_bridge_forwarding.py`, and `tests/unit/test_proxy_api_responses_contract.py`
- **Behavior**: equal idle/budget deadline ties now surface as `stream_idle_timeout`; younger multiplexed pending requests are preserved; downstream SSE connections receive keepalive frames while waiting.
