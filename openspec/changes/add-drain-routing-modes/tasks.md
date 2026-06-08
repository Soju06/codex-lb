## 1. Implementation

- [x] 1.1 Add sequential-drain and reset-drain selection to the canonical balancer.
- [x] 1.2 Add single-account routing using the configured account id.
- [x] 1.3 Persist and expose `single_account_id` in dashboard settings.
- [x] 1.4 Add dashboard controls, labels, schema coverage, and mocks for the new modes.
- [x] 1.5 Keep budget-safe fallback from replacing explicit drain or single-account routing.

## 2. Verification

- [x] 2.1 Add unit coverage for sequential-drain, reset-drain, and single-account selection.
- [x] 2.2 Add settings API and migration coverage for `single_account_id`.
- [x] 2.3 Add frontend schema, settings, and label coverage.
- [x] 2.4 Run focused backend tests.
- [x] 2.5 Run focused frontend tests.
- [x] 2.6 Run OpenSpec validation.
