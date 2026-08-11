## 1. Contract

- [x] 1.1 Record the complete synthetic Codex compaction SSE lifecycle in the
  `responses-api-compat` delta.
- [x] 1.2 Preserve the existing encrypted-item ID constraint and explicitly
  reject generated replacement IDs.

## 2. Implementation

- [x] 2.1 Preserve non-empty upstream compaction item status during
  normalization.
- [x] 2.2 Emit created, added, done, and completed events with monotonic
  sequence numbers.
- [x] 2.3 Reuse one terminal compaction item across the done event and completed
  response.

## 3. Verification

- [x] 3.1 Add focused unit coverage for event order, sequence numbers, status,
  ID, encrypted-content, usage, and `[DONE]`.
- [x] 3.2 Run focused tests, lint/type checks, OpenSpec validation, and diff
  hygiene checks.
