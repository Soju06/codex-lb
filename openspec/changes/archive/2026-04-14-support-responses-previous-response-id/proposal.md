## Why
Codex CLI websocket/resume flows now send previous_response_id for incremental Responses requests. codex-lb still rejects that field, causing websocket-enabled exec/resume failures and leaving users stuck on websocket-off fallback paths with degraded cache behavior.

## What Changes
- Allow and forward previous_response_id on Responses requests where upstream accepts it.
- Preserve conflict validation between conversation and previous_response_id.
- Add regression coverage for websocket and HTTP forwarding paths.
- Verify real cache behavior locally with docker compose across websocket on/off and /v1/responses variants.

## Impact
- Restores compatibility with newer Codex CLI websocket flows.
- Enables direct measurement of whether cache behavior returns to expected levels.
