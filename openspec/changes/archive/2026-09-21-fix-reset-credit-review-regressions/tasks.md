## 1. Backend reliability

- [x] 1.1 R1: preserve queued account lifetime and cover real scheduler/session completion.
- [x] 1.2 R2: add bounded cancellation-safe receipt settlement in dashboard, automatic and v1 paths.
- [x] 1.3 R3: reconcile manual credit-level receipts across distinct request IDs.
- [x] 1.4 R4: preserve authoritative availability counts during reconciliation.
- [x] 1.5 R7: preserve duplicate identity in targeted summaries with targeted lookup.
- [x] 1.6 R8: schedule persisted expiry-aware retries and test transient recovery near expiry.

## 2. Frontend reconciliation

- [x] 2.1 R5: match backend aggregate sample eligibility with regression coverage.
- [x] 2.2 R6: preserve pending state and newer summaries across polling with regression coverage.

## 3. Verification

- [x] 3.1 Run focused backend/frontend suites, type/lint/architecture checks and strict scoped specs.
- [x] 3.2 Complete independent re-review and resolve actionable findings.
- [x] 3.3 Record evidence and limitations, sync main specs/context, verify and archive.
