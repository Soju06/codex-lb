## 1. Specification
- [x] Add a responses-api-compat delta for Codex ChatGPT WebSocket wrapped-error parity.

## 2. Tests
- [x] Add unit coverage for top-level WebSocket error-detail normalization (`error_type` must not be confused with the event discriminator).
- [x] Add unit coverage for `status_code` alias handling.
- [x] Add/extend backend WebSocket previous-response masking coverage for official wrapped-error shapes.

## 3. Implementation
- [x] Normalize top-level `type: "error"` frames into an error-detail mapping before classification.
- [x] Accept `status_code` as a wrapped WebSocket HTTP status alias.
- [x] Preserve existing `stream_incomplete` masking for previous-response continuity misses.

## 4. Verification
- [x] Run focused websocket/error regression tests.
- [x] Run OpenSpec validation.
- [x] Run lint/format checks for changed files.
