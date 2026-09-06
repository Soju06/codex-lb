## 1. Regression

- [x] 1.1 Add a native helper protocol regression proving that absent inbound `Accept-Encoding` remains absent at the origin.
- [x] 1.2 Adjust the compressed-response regression to prove that a present inbound value reaches the origin unchanged while gzip response bytes are decoded and stale encoded-entity headers are removed.
- [x] 1.3 Capture RED for the new absence regression on the previous pull-request head and GREEN after the client-pool change.

## 2. Implementation

- [x] 2.1 Partition native HTTP clients by whether response decoding is enabled and disable all compiled reqwest decoders for requests without inbound compression negotiation.
- [x] 2.2 Restore forwarding of inbound `Accept-Encoding` values.
- [x] 2.3 Enable gzip, deflate, Brotli, and zstd response decoders for requests that carry compression negotiation.

## 3. Verification

- [x] 3.1 Run focused native helper protocol tests and `codex-lb-egress` unit tests.
- [x] 3.2 Run formatting, Clippy, the documented Rust gate, and strict scoped OpenSpec validation.
- [x] 3.3 Review the final change for scope and unchanged routing, replay, cancellation, and WebSocket behavior.
