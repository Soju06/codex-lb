# support-codex-desktop-tool-surface

## Why

`codex-lb` proxies the native `/backend-api/codex/responses` transport used by Codex Desktop, but it currently validates that traffic with the same built-in tool restrictions as the OpenAI-compatible `/v1/*` APIs. That blocks first-party Desktop requests that advertise the Codex tool surface, causing native websocket turns to fail with `400 Invalid request payload` and `param:"tools"` before the request reaches the upstream Codex backend.

## What Changes

- Allow the native `/backend-api/codex/responses` HTTP and websocket routes to validate tool payloads with the Codex-native tool surface enabled.
- Keep `/v1/responses` and `/v1/chat/completions` behavior unchanged so OpenAI-compat callers still receive invalid_request_error for unsupported built-in tools.
- Add regression coverage for native backend HTTP and websocket requests that include custom tools and built-in Codex tool types.

## Impact

- Codex Desktop can use `codex-lb` over tailnet without failing on native `response.create.tools`.
- OpenAI-compatible `/v1/*` callers keep the existing compatibility contract and error behavior.
