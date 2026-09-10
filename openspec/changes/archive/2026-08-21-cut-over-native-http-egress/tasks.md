## 1. Regression

- [x] 1.1 Add native response adapter tests for headers, buffered reads, single-consumer streaming, cancellation, and helper error provenance.
- [x] 1.2 Add direct Responses tests proving native selection, unavailable-only fallback, no ambiguous-failure replay, and unchanged SSE/error behavior.
- [x] 1.3 Add model-discovery tests proving native selection and unavailable fallback.
- [x] 1.4 Add container/package assertions for the locked native helper artifact.

## 2. Implementation

- [x] 2.1 Add fixed-path runtime discovery and complete the native response/error contract.
- [x] 2.2 Cut direct Responses HTTP/SSE over to native egress while preserving policy, observability, and cancellation.
- [x] 2.3 Cut direct model discovery over to native egress.
- [x] 2.4 Build and install the locked helper in the official Linux container without adding required setup.

## 3. Verification

- [x] 3.1 Run focused and broad proxy tests, formatting, lint, type checks, Rust checks, Docker contract tests, and strict OpenSpec validation.
- [x] 3.2 Repeat authenticated direct-vs-LB HTTP/SSE and model captures, recording HTTP/2/TLS closure and remaining connection-reuse, routed, WebSocket, and egress-origin gaps.
