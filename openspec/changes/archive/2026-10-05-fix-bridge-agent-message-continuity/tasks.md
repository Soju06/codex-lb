## 1. Reproduce

- [x] 1.1 Add a failing HTTP-route regression for a full resend ending in an encrypted agent message on a fresh bridge.

## 2. Implement

- [x] 2.1 Extend only same-owner context proofs to recognize agent-message follow-ups.
- [x] 2.2 Preserve original input, owner pinning, and existing replay fences.
- [x] 2.3 Cover stale-anchor rejection, missing tool context, malformed messages, and unavailable owners.

## 3. Verify

- [x] 3.1 Run focused unit and route-level regressions, then relevant proxy suites.
- [x] 3.2 Run lint, type checks, and strict OpenSpec validation.
- [x] 3.3 Sync the specification, record verification, and archive the change.
