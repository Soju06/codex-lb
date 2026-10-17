## Why

After a completed Codex turn, an idle upstream WebSocket can close while the downstream remains open. The client can then send a delta under the false assumption that its upstream context still exists.

## What Changes

- Close the ordinary Codex downstream with reconnect code 1012 when its completed, idle upstream is lost.
- Retire only the continuity anchor belonging to that physical connection.
- Preserve existing handling for active or replay-owned requests and public OpenAI-compatible WebSockets.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `responses-api-compat`: notify the client when an idle Codex upstream context is lost.

## Impact

Direct WebSocket lifecycle and regression tests. No settings, schema, routing-policy or native burst-buffer changes. This does not reconstruct lost upstream context.
