## 1. Implementation

- [x] 1.1 Add bounded pre-visible refresh/connect failover for thread-goal, Codex control, transcription, and file create/finalize paths.
- [x] 1.2 Record the failed account and exclude it from the current unary request before selecting a fallback account.
- [x] 1.3 Preserve strict account-owner fail-closed behavior for pinned file finalization.

## 2. Tests

- [x] 2.1 Add unit coverage proving thread-goal, Codex control, transcription, and file create retryable refresh/connect failures use another eligible account.
- [x] 2.2 Add unit coverage proving pinned file finalization does not fail over to another account.

## 3. OpenSpec

- [x] 3.1 Add a `responses-api-compat` delta for pre-visible unary refresh/connect failover.
- [x] 3.2 Validate the OpenSpec change.
