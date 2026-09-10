# Change: Align Codex Responses wire identity

## Why

Live direct-versus-load-balanced captures of Codex CLI 0.150.1 show that the
selected account installation id belongs in `response.create.client_metadata`,
not in a standalone HTTP or WebSocket handshake header. They also show that an
LB-generated downstream turn-state token is not present on the initial direct
Codex WebSocket handshake. Emitting either value in the wrong wire location
makes otherwise equivalent codex-lb traffic distinguishable upstream.

## What Changes

- Describe the observed Codex Responses HTTP/SSE and WebSocket identity shape
  as an explicit, transport-aware wire profile.
- Keep canonical selected-account installation metadata in every
  `response.create` payload while omitting the standalone installation header
  on profiled Responses egress.
- Keep synthesized turn state as downstream and internal affinity state without
  forwarding it on the initial upstream WebSocket handshake.
- Continue to preserve genuine client-provided and upstream-issued continuity
  values where the existing continuity contract requires them.

## Impact

- Affected specs: `upstream-proxy-routing`, `responses-api-compat`
- Affected code: Responses HTTP/WebSocket header shaping, WebSocket route
  forwarding, and parity regression tests
