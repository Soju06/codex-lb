## 1. Regression Coverage

- [x] 1.1 Update the compaction-trigger integration test to require the upstream `cmp_*` ID in both synthetic SSE events.
- [x] 1.2 Add focused contract coverage for ID preservation from remote-compaction-v2 `output` and `compaction_summary` shapes.

## 2. Implementation

- [x] 2.1 Preserve a valid upstream compaction item ID in Codex-compatible compact output normalization.
- [x] 2.2 Confirm standalone compact normalization and trigger streaming emit the same item contract.

## 3. Verification

- [x] 3.1 Run focused unit and integration tests for compact response normalization and compaction-trigger streaming.
- [x] 3.2 Run lint, type checking, strict change validation, and the full OpenSpec spec validation suite.
