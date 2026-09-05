## 1. Regression

- [x] 1.1 Add adapter tests for native handshake, text/binary frames, concurrent
  send/receive acknowledgement routing, close semantics, cancellation, helper
  death, and unavailable-only fallback.
- [x] 1.2 Add connector tests proving direct Responses and Live WebSockets use
  native egress while routed connections retain the existing client.
- [x] 1.3 Add Rust or deterministic integration coverage for compression,
  subprotocol/headers, size limits, control frames, and multiplexed sockets.

## 2. Implementation

- [x] 2.1 Extend the helper protocol and active-request registry with native
  WebSocket tasks and targeted commands.
- [x] 2.2 Add the Codex-pinned OpenAI tungstenite forks and native TLS/proxy
  connector behavior.
- [x] 2.3 Add the Python native WebSocket adapter and direct connector cutover
  without changing routed failover/account ownership.
- [x] 2.4 Update protocol, operator, and traffic-parity documentation.

## 3. Verification

- [x] 3.1 Run focused and broad Python tests, lint/type checks, Rust checks/tests,
  Docker artifact build, and strict OpenSpec validation.
- [x] 3.2 Run a local bidirectional wire probe and an authenticated upstream
  comparison when the isolated credentials remain available.
