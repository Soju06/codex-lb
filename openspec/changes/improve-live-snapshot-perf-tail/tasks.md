## 1. Implementation

- [x] 1.1 Add perf-tail grouping to the live snapshot request-log report.
- [x] 1.2 Add request-log query timing metadata.
- [x] 1.3 Preserve explicit malformed container PostgreSQL JSON errors.
- [x] 1.4 Add a separate websocket-instability summary with hashed account ids.

## 2. Verification

- [x] 2.1 Add focused unit coverage for perf-tail grouping, guidance labels, query timing metadata, websocket instability grouping, and malformed JSON handling.
- [x] 2.2 Run focused live snapshot unit tests.
- [x] 2.3 Run `openspec validate improve-live-snapshot-perf-tail --type change --strict`.
- [x] 2.4 Run `openspec validate --specs`.
- [x] 2.5 Run the updated live snapshot against `codex-lb-direct`.
