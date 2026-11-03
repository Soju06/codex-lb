## 1. Reproduction and implementation

- [x] 1.1 Reproduce stuck reset recovery with synthetic endpoint tests; verify failures on v1.24.0.
- [x] 1.2 Share routed wake and refresh across consume paths; verify stale, already-fresh, failed-fetch, and proxy-binding cases.
- [x] 1.3 Recover confirmed reset blocks and weekly-only accounts; verify cooldown, concurrency, and hard-block guards.
- [x] 1.4 Reconcile runtime state and badge eligibility; verify selection and summary parity.
- [x] 1.5 Bound natural rollover wake attempts durably; verify repeated ticks, concurrent attempts, and subsequent windows.

## 2. Validation

- [x] 2.1 Run focused and full pytest suites with bounded CPU; record exact results.
- [x] 2.2 Run lint, types, architecture, and strict OpenSpec validation; inspect changes for credentials and scope.
- [x] 2.3 Sync verified requirements and archive this change; PR publication is tracked by the parent implementation plan.

Validation: 34 reset regressions passed; 564 affected tests passed with 3 existing skips; full pytest completed with 8,679 passed, 106 skipped, and 4 failures. Two dependency-absence tests fail identically on the untouched tag. One logging assertion passes when the full-run logger-disable option is removed, and the subprocess readiness failure passes on both versions when rerun. The three affected files rerun with normal logging produced the same 18 passes and 2 baseline failures on both versions. Frontend: 1,148 tests passed across 146 files, and the build passed. Ruff, formatting, types, architecture, and strict change validation passed. All-spec validation retains the same eight failures as v1.24.0. No live deployment was tested.
