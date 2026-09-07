## 1. Reproduction

- [x] 1.1 Reproduce expired-token plus permanent forced-refresh failure through the HTTP Responses route, including a second independent message.
- [x] 1.2 Add controls for successful refresh, repeated 401, hard ownership, opaque input, and post-output failure.

## 2. Implementation

- [x] 2.1 Persist access-authentication invalidation with credential-generation guards and honor it in selection and routing snapshots.
- [x] 2.2 Add bounded account-neutral replay for pre-visible authentication recovery and preserve the original terminal error otherwise.
- [x] 2.3 Verify lease release, settlement-before-health ordering, stale repair guards, and routing restoration.

## 3. Verification

- [x] 3.1 Run focused unit/integration regressions, lint, type checks, architecture/timing checks, and strict OpenSpec validation.
- [x] 3.2 Review the final diff and prepare a focused upstream-main PR with related-PR coordination notes.
