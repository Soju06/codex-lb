## 1. OpenSpec Contract

- [x] 1.1 Validate the proposal, design, context, and `api-keys` delta strictly.

## 2. Compact Reservation Settlement

- [x] 2.1 Settle the held API-key reservation before the pre-freshness and no-freshness-reserve budget terminals.
- [x] 2.2 Settle the held API-key reservation before the post-freshness budget terminal.
- [x] 2.3 Settle the held API-key reservation before the post-401 forced-refresh budget terminal.
- [x] 2.4 Preserve the inner `_call_compact` settlement path without adding a second settlement.

## 3. Regression Coverage

- [x] 3.1 Add route-level coverage proving preflight budget exhaustion settles before returning the unchanged 502 error.
- [x] 3.2 Add focused coverage proving inner compact-call budget exhaustion settles exactly once.

## 4. Validation

- [x] 4.1 Run focused compact and API-key reservation tests.
- [x] 4.2 Run Ruff formatting/checks, type and architecture checks, strict change validation, and main-spec validation.
- [x] 4.3 Verify implementation completeness, correctness, and coherence against the change artifacts.
