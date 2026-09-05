## 1. Contract and regression

- [x] 1.1 Add routed HTTP tests for native preference, proxy endpoint order,
  request body shapes, unavailable-only Python fallback, and ambiguous failure.
- [x] 1.2 Add routed WebSocket tests for native preference, endpoint fallback,
  handshake denial, TLS provenance, relay wrapping, and cleanup.
- [x] 1.3 Extend native error protocol tests for TLS verification provenance and
  credential-safe failures.

## 2. Implementation

- [x] 2.1 Add native single-attempt HTTP preparation and execution to
  `CodexClient` without moving route policy into the helper.
- [x] 2.2 Add native routed WebSocket execution and select the correct relay
  adapter while preserving route metadata.
- [x] 2.3 Update native helper typed error metadata and operator documentation.

## 3. Verification

- [x] 3.1 Run focused and broad Python tests, Rust checks/tests, lint/type
  checks, Docker locked build, and strict OpenSpec validation.
- [x] 3.2 Run local HTTP/SSE/WebSocket proxy probes proving that direct and
  routed attempts use the same native helper generation.
