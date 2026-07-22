## 1. Backend Contract

- [x] 1.1 Add failing Reports API and service regressions for the exact inverted-range error, pre-repository rejection, and valid one-day and 730-day controls.
- [x] 1.2 Add the typed Reports date-order domain error and reject defaulted inverted bounds before date conversion or repository work.
- [x] 1.3 Map the domain error to the exact `invalid_report_date_range` dashboard HTTP 400 envelope.

## 2. Reports UI

- [x] 2.1 Add failing frontend regressions at the real `<App />` `/reports` route for reciprocal date bounds, linked accessible invalid state, distinct-query suppression, Accounts-only Retry, and recovery through either corrected bound.
- [x] 2.2 Add shared Reports date-order validation, reciprocal native input bounds, and localized corrective copy for every supported locale.
- [x] 2.3 Disable both automatic and manual Reports query paths while date order is invalid, keep Accounts retryable, and resume Reports with corrected bounds.

## 3. Verification

- [x] 3.1 Run focused backend and frontend regressions, including the strict dual-mode HTTP reproduction.
- [x] 3.2 Run relevant full Python and frontend lint, typecheck, test, build, architecture, and OpenSpec validation gates.
- [x] 3.3 Complete browser smoke for inverted input and correction, saving before/after evidence outside the repository.
- [x] 3.4 Verify OpenSpec completeness/coherence, LSP diagnostics, isolated worktree diff, and untouched canonical checkout.
