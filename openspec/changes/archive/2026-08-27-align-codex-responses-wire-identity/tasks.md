## 1. Contract

- [x] 1.1 Record the Responses wire-profile requirements for installation id
  location and synthesized turn-state provenance.

## 2. Implementation

- [x] 2.1 Add a transport-aware Codex wire profile and apply it to Responses
  HTTP/SSE and WebSocket egress.
- [x] 2.2 Stop copying LB-synthesized downstream turn state into initial
  upstream WebSocket handshake headers while retaining internal continuity.

## 3. Verification

- [x] 3.1 Add focused regression coverage for header absence, canonical payload
  metadata, downstream accept state, and internal affinity continuity.
- [x] 3.2 Run focused tests, lint, strict OpenSpec validation, and a live
  direct-versus-load-balanced capture comparison.
