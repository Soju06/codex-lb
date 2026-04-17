# allow-responses-built-in-tools

## Why
Recent Codex and Codex for VS Code builds send built-in Responses tool definitions such as `image_generation`, `computer_use`, and `code_interpreter`. `codex-lb` currently rejects those payloads locally with `invalid_request_error` and `param = "tools"` before the request can reach upstream, which breaks otherwise valid Responses-family clients.

## What Changes
- Allow built-in tool definitions in Responses-family request validation (`/v1/responses`, `/backend-api/codex/responses`, and websocket `response.create` payloads).
- Preserve the existing `web_search_preview -> web_search` normalization for Responses requests.
- Keep Chat Completions validation strict so unsupported built-in tools are still rejected on `/v1/chat/completions`.

## Impact
- Newer Codex clients can continue using Responses-family endpoints through `codex-lb`.
- Chat Completions behavior remains unchanged and does not silently broaden built-in tool support.
