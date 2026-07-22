## 1. Characterize the existing contract

- [x] 1.1 Add focused regression coverage for candidate-cycle filters, representative selection, manual/scheduled ordering, model snapshots, account snapshot membership, dynamic statuses, and empty offsets.
- [x] 1.2 Add repository query-count capture that distinguishes grouped page selection and filter-option facets from later progress enrichment.

## 2. Consolidate grouped page selection

- [x] 2.1 Extract shared candidate-cycle and representative-run query primitives without changing filter semantics.
- [x] 2.2 Add the lightweight no-status cycle scope and keep the existing eligibility/effective-status expressions in one reusable full-status scope.
- [x] 2.3 Return representative run rows and `COUNT(*) OVER ()` totals in one page statement, with one exact-count fallback only for an empty out-of-range offset.

## 3. Consolidate filter options

- [x] 3.1 Narrow the repository option record to the account and model facets consumed by the service while retaining canonical service-level status and trigger choices.
- [x] 3.2 Combine account and model facet selection into one portable tagged query and preserve the existing status/no-status account semantics.

## 4. Cross-backend regression and performance evidence

- [x] 4.1 Prove SQLite semantics and ratchet a non-empty grouped page to one selection statement, an out-of-range page to at most two, and option facets to one.
- [x] 4.2 Run the same semantic and query-count coverage on PostgreSQL, wire the focused contract tests into `POSTGRES_PYTEST_TARGETS`, and capture representative before/after query-plan or benchmark evidence.

## 5. Verification

- [x] 5.1 Run focused automation repository/API tests and the appropriate broader unit/integration suites.
- [x] 5.2 Run Ruff format/check, `ty`, architecture, simplicity, diff checks, and strict OpenSpec validation.
- [x] 5.3 Formally verify every requirement/scenario against implementation evidence and perform an adversarial standalone-diff review.
