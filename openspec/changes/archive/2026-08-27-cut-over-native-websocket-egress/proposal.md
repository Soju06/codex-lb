## Why

Direct HTTP and SSE already leave codex-lb through the persistent Rust helper,
but direct WebSocket handshakes and frames still use Python `websockets`. That
leaves the largest library-level difference from the Codex CLI on the Responses
WebSocket path: TLS construction, handshake serialization, compression
negotiation, frame masking, and ping/pong behavior.

## What Changes

- Extend the persistent helper protocol with multiplexed WebSocket connect,
  send, receive, close, and cancel commands.
- Build direct WebSockets with the exact Codex 0.150.1 OpenAI forks of
  `tokio-tungstenite` and `tungstenite`, including default permessage-deflate.
- Cut direct Responses and Live WebSockets over to the native helper while
  preserving account-routed WebSockets on the route-aware Python client.
- Preserve the unavailable-only fallback boundary: only absence of the helper
  before dispatch permits the existing Python direct connector.
- Retain handshake status/header/body mapping, message-size limits, selected
  subprotocols, close codes, and credential-safe Live errors.

## Impact

- Affected specs: `outbound-http-clients`, `responses-api-compat`,
  `compatibility-tooling`.
- Affected code: persistent Python helper adapter, upstream WebSocket connector,
  Rust native helper protocol and dependencies, packaging tests, and traffic
  parity documentation.
- No new runtime setting or required dependency for non-container installs.
