## 1. Contract and classification

- [x] 1.1 Record whether a prepared request is both a Codex prewarm and
  `generate = false`.
- [x] 1.2 Count matched direct-WebSocket response progress events accurately.
- [x] 1.3 Allow only the created-only sequence-zero prewarm through the existing
  one-shot replay guard.

## 2. Sequence and lifecycle safety

- [x] 2.1 Reject a replay event whose numeric sequence does not advance beyond
  the exposed watermark.
- [x] 2.2 Preserve the existing 1011 close, single settlement, request logging,
  and reservation/admission cleanup on replay refusal.
- [x] 2.3 Preserve fail-closed behavior for ordinary turns, metadata-only
  prewarm claims, progressed prewarms, non-zero watermarks, and multiple
  pending requests.

## 3. Regression coverage

- [x] 3.1 Add a direct `/backend-api/codex/responses` product-path regression
  for abnormal close after sequenced prewarm `response.created`.
- [x] 3.2 Add focused regressions for request classification, progress
  accounting, and every replay-refusal boundary.
- [x] 3.3 Verify recovered prewarms emit one downstream `response.created`, one
  advancing terminal event, and finalize/log exactly once.

## 4. Validation

- [x] 4.1 Run focused unit and direct-WebSocket integration tests.
- [x] 4.2 Run changed-file lint/format, type checking, architecture checks, and
  strict OpenSpec validation.
- [x] 4.3 Review the final diff against the sequence, account ownership,
  settlement, and simplicity invariants before opening the PR.
