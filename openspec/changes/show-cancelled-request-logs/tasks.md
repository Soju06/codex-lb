## 1. Backend Request Logs contract

- [x] 1.1 Add failing public API regressions for unfiltered, cancelled-filter,
  error-filter, and status-option behavior
- [x] 1.2 Include cancelled rows in raw listing predicates, rollup-backed
  totals, and cache signatures
- [x] 1.3 Expose persisted cancelled rows as public status `cancelled`

## 2. Frontend presentation

- [x] 2.1 Add a failing rendered-table regression for the localized,
  non-error cancelled badge
- [x] 2.2 Add cancelled status fallback text, locale strings, and a distinct
  existing-badge treatment

## 3. Validation

- [x] 3.1 Run focused backend and frontend regressions
- [x] 3.2 Run affected lint, type, build, and OpenSpec validation
- [x] 3.3 Exercise unfiltered, cancelled-filter, and error-filter behavior
  through the running dashboard and capture before/after evidence
