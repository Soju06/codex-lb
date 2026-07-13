## 1. Implementation

- [x] 1.1 `parse_sse_event_payload` in parsing.py; `parse_sse_event` delegates
- [x] 1.2 Mixin + bridge upstream reader: single parse per event, validate from payload, thread event into the tool-call rewrite
- [x] 1.3 Tool-call rewrite helpers: accept parsed event, no re-parse on no-change, validate rewritten payload directly
- [x] 1.4 /v1 public normalizer identity-gated raw pass-through; reasoning normalizer raw-block buffering

## 2. Validation

- [x] 2.1 Full unit + integration + bridge/ws/e2e suites green (byte-level SSE assertions throughout the existing suites act as the parity oracle)
- [x] 2.2 Microbenchmark: mixin layer 21.4 → 12.1 µs/event
- [x] 2.3 `openspec validate --specs`, `ruff`, `ty`
