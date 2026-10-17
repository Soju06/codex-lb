## 1. Reconcile and verify the implementation

- [x] 1.1 Compare current main and related shutdown PRs; preserve their distinct contracts.
- [x] 1.2 Bound the managed HTTP close wait and retain its task through completion.
- [x] 1.3 Cover prompt completion, timeout, failure and self-cancellation.
- [x] 1.4 Prove a stalled close no longer prevents lifespan process-death marking and database disposal; reproduce on the baseline.
- [x] 1.5 Run focused lifecycle tests, lint and type checks.
- [ ] 1.6 Complete independent review, local CI and scoped strict OpenSpec verification.
