# Fix WebSocket pre-created heartbeat gap

## Why

Codex WebSocket clients reset their stream idle watchdog only when an application text frame reaches the client. WebSocket protocol ping/pong frames are handled below that layer and do not reset Codex's `idle timeout waiting for websocket` timer.

codex-lb already emits `response.in_progress` keepalives after upstream assigns a `response.id`, but it deliberately emits nothing before `response.created`. If ChatGPT stays silent before assigning the response id while the proxy is still actively waiting, Codex clients can disconnect even though the upstream WebSocket transport is healthy.

## What Changes

- Emit a Codex-native vendor heartbeat for pending Codex WebSocket requests that do not yet have a `response.id`.
- Preserve existing `response.in_progress` keepalives once `response.created` assigns a response id.
- Keep public `/v1/responses` WebSocket behavior unchanged: no vendor pre-created heartbeat on OpenAI-style WebSocket sessions.
- Add regression coverage for the pre-created silence gap and for public `/v1` isolation.

## Impact

Codex CLI WebSocket turns survive extended upstream silence before `response.created`. OpenAI-style `/v1/responses` WebSocket clients do not receive new `codex.*` vendor frames.
