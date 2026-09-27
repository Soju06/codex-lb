## 1. Baseline

- [x] 1.1 Reproduce missing cache-write cost through actual HTTP and stored usage.
- [x] 1.2 Read and pass the relevant existing pricing and settlement regressions.

## 2. Implementation

- [x] 2.1 Preserve explicit cache-write prices and implement disjoint input costing.
- [x] 2.2 Preserve write counts through upstream usage, logs and API-key settlement.
- [x] 2.3 Add nullable persistence with historical-row upgrade/downgrade coverage.

## 3. Verification

- [x] 3.1 Prove HTTP request cost and read-side cost with captured red/green evidence.
- [x] 3.2 Prove keyed settlement and existing finalization behavior.
- [x] 3.3 Prove missing/invalid counts, tiers, context boundaries and legacy behavior.
- [x] 3.4 Pass scoped tests, diagnostics, lint/types, migration and OpenSpec checks.
- [x] 3.5 Clean owned QA resources and record self-review.

## 4. Documentation

- [x] 4.1 Sync verified requirements and record local verification.
- [ ] 4.2 Archive the verified change.
