## Why

`Codex CLI` can use the Responses-over-WebSocket transport when the provider advertises websocket support and the selected model prefers websockets. `codex-lb` currently exposes only HTTP/SSE on `/backend-api/codex/responses` and `/v1/responses`, so clients configured with `supports_websockets = true` cannot proxy the websocket flow through the load balancer.

## What Changes

- Add websocket support on `/backend-api/codex/responses` and `/v1/responses`.
- Proxy websocket frames to the upstream ChatGPT Codex websocket endpoint on the selected account.
- Preserve existing load-balancer account selection, session affinity, request logging, and API key limit settlement for websocket requests.
- Document the websocket transport contract in OpenSpec and the Codex CLI setup in `README.md`.

## Impact

- Code: `app/core/auth/dependencies.py`, `app/core/clients/proxy_websocket.py`, `app/core/middleware/api_firewall.py`, `app/modules/proxy/api.py`, `app/modules/proxy/service.py`
- Tests: `tests/integration/test_proxy_websocket_responses.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`
