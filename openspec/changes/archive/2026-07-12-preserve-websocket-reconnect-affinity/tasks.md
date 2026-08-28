## 1. Specification

- [x] 1.1 Define the distinction between current-handshake generated and
  client-supplied turn states.

## 2. Implementation

- [x] 2.1 Carry generated-turn-state provenance from the WebSocket routes to
  affinity and continuity selection.
- [x] 2.2 Prefer durable session or prompt-cache affinity over only a
  current-handshake generated turn state.
- [x] 2.3 Preserve upstream forwarding and client-echoed turn-state behavior.
- [x] 2.4 Seed generated turn states for client-echoed reconnect continuity.

## 3. Verification

- [x] 3.1 Add unit and integration reconnect regressions.
- [x] 3.2 Run targeted tests, lint, type checks, and strict OpenSpec
  validation.
