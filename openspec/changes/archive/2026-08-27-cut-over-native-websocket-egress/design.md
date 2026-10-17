## Context

The helper is already a per-worker, multiplexed process for direct HTTP/SSE.
WebSockets are bidirectional and long lived, so they need request-scoped command
channels rather than the response-only HTTP event stream. Codex 0.150.1 uses
OpenAI-maintained `tokio-tungstenite` and `tungstenite` revisions with
permessage-deflate enabled.

## Goals / Non-Goals

**Goals:**

- Match the Codex Rust WebSocket implementation family for direct upstream
  handshakes, TLS, compression, masking, and control frames.
- Multiplex multiple WebSockets alongside HTTP requests in one helper without
  cross-delivering frames or acknowledgements.
- Preserve existing direct Responses and Live WebSocket application contracts.
- Make every post-dispatch failure terminal for that connection.

**Non-Goals:**

- Replacing account-routed WebSocket failover and health accounting.
- Sharing WebSocket connections across downstream clients or workers.
- Making source IP/ASN or network timing identical.
- Reusing a WebSocket after it has closed or replaying an ambiguous frame.

## Decisions

- Add `websocket_connect`, `websocket_send_text`, `websocket_send_binary`, and
  `websocket_close` commands. All carry the connection `request_id`; send and
  close commands also carry a `command_id` acknowledged only after the native
  sink accepts the frame.
- Rust keeps one bounded command channel per active WebSocket and one task that
  owns its stream. That task answers peer pings, emits periodic liveness pings,
  enforces the configured pong deadline, suppresses control-frame events, and
  emits text, binary, close, error, and send-ack events.
- The WebSocket configuration enables the fork's default permessage-deflate and
  applies the existing maximum decompressed message size.
- Python runs one pump per native WebSocket. The pump separates frame delivery
  from send acknowledgements so caller send and receive coroutines cannot steal
  each other's protocol events.
- Direct native handshake errors retain status, response headers, and a bounded
  body for the existing error mapper. Logs and generic helper messages remain
  credential-safe.
- Absence of the helper before `websocket_connect` uses the existing Python
  connector. Helper exit, protocol failure, timeout, handshake denial, or a
  failed frame after command dispatch never falls back or replays.
- Account-routed WebSockets continue through `CodexClient`; they depend on route
  metadata, endpoint failover, account-health provenance, and aiohttp context
  ownership that are intentionally outside the native direct boundary.

## Risks / Trade-offs

- [A slow downstream can backpressure helper demultiplexing] -> Keep bounded
  per-connection queues and ensure every relay terminal path closes or cancels.
- [Send acknowledgement races with remote close] -> Resolve pending sends with
  the first native error/close and never retry the frame.
- [Handshake body leaks credentials] -> Bound and pass it only to the existing
  sanitizer/parser; never include it in helper error text or logs.
- [Native dependency drift changes wire behavior] -> Pin the exact OpenAI fork
  revisions observed in Codex 0.150.1 and assert them in packaging tests.
