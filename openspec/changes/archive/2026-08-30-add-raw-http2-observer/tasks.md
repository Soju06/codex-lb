## 1. Regression

- [x] 1.1 Add raw frame parser tests for fragmented prefaces/frames, ordered
  SETTINGS, flow control, header fragments, bounds, and forbidden raw payloads.
- [x] 1.2 Add deterministic h2 client/server tests for model, HTTP JSON, SSE,
  multiple streams, and privacy-safe capture records.
- [x] 1.3 Add comparator/report tests for exact match, SETTINGS mismatch,
  header-order mismatch, reuse difference, A′ context, and missing evidence.

## 2. Implementation

- [x] 2.1 Add the loopback-safe TLS HTTP/2 observer and deterministic origin.
- [x] 2.2 Add bounded privacy-safe JSONL records for connection and request wire
  profiles.
- [x] 2.3 Add the A/A′/C HTTP/2 comparison CLI and Markdown/JSON rendering.
- [x] 2.4 Document certificate setup, controlled runs, evidence interpretation,
  and remaining HPACK/TCP limits.

## 3. Verification

- [x] 3.1 Run focused and broad traffic-tooling tests, formatting, lint, type
  checks, strict OpenSpec validation, and a local TLS h2 smoke.
