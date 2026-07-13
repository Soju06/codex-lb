## 1. Spec

- [x] 1.1 Add a `frontend-architecture` delta for reports chart continuity, Daily Breakdown sorting, and cached-token cell rendering.
- [x] 1.2 Extend the same `frontend-architecture` delta for the Tokens summary cache subtitle, chronological CSV export, and visible sort-state icons.

## 2. Shared daily series

- [x] 2.1 Extract the reports continuous-day fill logic into a shared helper within `frontend/src/features/reports`.
- [x] 2.2 Update `CostPerDayChart` and `TokensPerDayChart` to render from the shared selected-range daily series.
- [x] 2.3 Update `DailyDetailTable` to consume the same shared selected-range daily series.

## 3. Daily Breakdown interactions

- [x] 3.1 Add sortable headers for `Day`, `Reqs`, `Input Tokens`, `Output Tokens`, `Cost`, and `Accounts`.
- [x] 3.2 Default the Daily Breakdown sort to `Day` descending and toggle direction when the active header is selected again.
- [x] 3.3 Render cached input tokens as muted secondary text inside the `Input Tokens` cell, including `0 (0)` when both values are zero.
- [x] 3.4 Render cached totals in the `/reports` Tokens summary subtitle as `Input ... · Cache ... · Output ...`.
- [x] 3.5 Export Daily Breakdown CSV rows in ascending `Day` order regardless of the current visible table sort.
- [x] 3.6 Render a muted unsorted icon for inactive sortable headers and a directional active icon for the sorted header.

## 4. Verification

- [x] 4.1 Add or update tests that prove both reports charts fill missing selected dates with zero-value rows.
- [x] 4.2 Add or update tests that prove Daily Breakdown defaults to day-descending order and can sort each visible column.
- [x] 4.3 Add or update tests that prove the `Input Tokens` cell renders cached tokens, including zero-value cases.
- [x] 4.4 Run `openspec validate improve-reports-daily-breakdown --strict`.
- [x] 4.5 Run the relevant frontend test targets for the reports charts and Daily Breakdown table.
- [x] 4.6 Add or update tests that prove the `/reports` Tokens summary subtitle includes cached totals.
- [x] 4.7 Add or update tests that prove CSV export stays chronological and sortable headers expose visible sort-state icons.
- [x] 4.8 Re-run `openspec validate improve-reports-daily-breakdown --strict` and the focused reports frontend tests.
