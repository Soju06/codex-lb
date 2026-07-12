# Preserve WebSocket reconnect affinity

## Why

When a downstream Responses WebSocket handshake has no
`x-codex-turn-state`, the proxy generates one and forwards it upstream. The
generated per-connection value currently takes precedence over a stable Codex
session header or prompt-cache key during account selection. A reconnect then
creates a new affinity key and can rotate accounts despite a durable client
continuity signal.

## What Changes

- Track whether a turn state was generated for the current downstream
  WebSocket handshake.
- Keep forwarding that generated turn state upstream, but let durable
  client-supplied session or prompt-cache affinity take precedence.
- Preserve client-supplied (including previously echoed) turn states as the
  most-specific affinity and continuity key.

## Impact

- **Spec**: `responses-api-compat`
- **Behavior**: backend Codex WebSocket reconnects with a stable session
  header keep the same account affinity even when each connection receives a
  newly generated turn state.
