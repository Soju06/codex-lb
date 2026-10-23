## 1. Reproduce and isolate

- [x] 1.1 Add route-level native full-resend regressions and demonstrate the failure on unchanged main.
- [x] 1.2 Add malformed-history and immutable-proof regression cases.

## 2. Implement

- [x] 2.1 Add the owner-bound tool-context proof without changing account-neutral eligibility or wire payloads.
- [x] 2.2 Wire fresh and stale-anchor recovery with matching-owner and fallback protection.
- [x] 2.3 Verify unavailable-owner, quarantine, replay fencing and subsequent-turn behavior.

## 3. Verify and document

- [ ] 3.1 Run focused regressions, lint and typing plus the full local gate; record actual limitations.
- [ ] 3.2 Independently review the final diff and address actionable findings.
- [ ] 3.3 Synchronize and validate requirements/context, then verify and archive the change.
