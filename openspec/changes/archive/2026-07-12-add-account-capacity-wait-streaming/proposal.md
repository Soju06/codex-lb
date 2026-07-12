## Why

When every otherwise eligible account is temporarily rate-limited or at stream capacity, streaming clients should not immediately fail as if no account can ever serve the request. They also must not sit on a silent socket or wait forever through repeated capped retry hints.

## What Changes

- Define a bounded account-capacity wait contract for streaming Responses traffic.
- Require downstream keepalive/progress events while the proxy is waiting for account capacity to recover.
- Keep the wait inside the original request budget so exhausted capacity eventually returns the normal no-account/rate-limit failure.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: streaming Responses routes may wait for recoverable account capacity, but must keep the client stream alive and stay within the request budget.
- `proxy-runtime-observability`: account-capacity waits must be visible through low-cardinality logs and downstream progress events.

## Impact

- Code: `app/modules/proxy/_service/support.py`, `app/modules/proxy/_service/streaming/retry.py`, `app/modules/proxy/_service/http_bridge/streaming.py`, `app/modules/proxy/_service/http_bridge/mixin.py`, `app/modules/proxy/_service/websocket/mixin.py`
- Tests: `tests/unit/test_proxy_utils.py`, `tests/unit/test_proxy_http_bridge.py`, `tests/integration/test_proxy_websocket_responses.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/proxy-runtime-observability/spec.md`
