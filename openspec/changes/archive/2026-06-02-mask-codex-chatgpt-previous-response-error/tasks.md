## 1. Specification
- [x] Add a responses-api-compat delta for top-level Codex ChatGPT WebSocket previous-response errors.

## 2. Tests
- [x] Add regression coverage for `/backend-api/codex/responses` receiving a top-level `previous_response_not_found` WebSocket error frame.
- [x] Verify existing nested previous-response masking regressions remain green.

## 3. Implementation
- [x] Broaden WebSocket error payload extraction to treat top-level `type: "error"` fields as an error payload when no nested `error` object exists.
- [x] Reuse existing `stream_incomplete` continuity masking behavior.

## 4. Verification
- [x] Run focused WebSocket previous-response regression tests.
- [x] Run OpenSpec validation.
