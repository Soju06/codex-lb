## Why

Newer Codex Desktop / Codex CLI builds send built-in Responses tool definitions (such as `image_generation`) in `/backend-api/codex/responses` and `/v1/responses` payloads, and the same definitions arrive in WebSocket `response.create` frames. codex-lb currently rejects those tools locally during Responses payload validation and returns `invalid_request_error` with `param = "tools"` before the request reaches upstream, breaking live clients. For Responses-family proxying, tool support should be decided by upstream unless the proxy itself cannot represent the payload.

Chat Completions is a separate compatibility surface whose tool allowlist is documented (`web_search` only) and still relied on by non-Codex clients; it must stay strict until we intentionally widen it.

## What Changes

- Allow built-in tools in Responses-family payloads, including HTTP `/v1/responses`, HTTP `/backend-api/codex/responses`, and their WebSocket equivalents.
- Continue forwarding accepted built-in tool definitions unchanged except for documented aliases (`web_search_preview` -> `web_search`).
- Preserve existing Chat Completions behavior: `/v1/chat/completions` continues to reject unsupported built-in tools other than `web_search` until that compatibility surface is intentionally expanded.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `responses-api-compat`: accept built-in Responses tools as pass-through payload data.

## Impact

- Code: `app/core/openai/requests.py`, `app/core/openai/chat_requests.py`
- Tests: `tests/unit/test_openai_requests.py`, `tests/integration/test_openai_compat_features.py`, `tests/integration/test_proxy_websocket_responses.py`
