# Tasks: add-reports-page

## 1. Specs
- [x] 1.1 Add `/api/reports` and reports page report-payload requirements.
- [x] 1.2 Update the reports donut requirements for model and user-agent center totals.
- [x] 1.3 Validate OpenSpec changes.

## 2. Backend
- [x] 2.1 Add nullable `accountId` handling in report DTO/schema and aggregation.
- [x] 2.2 Use SQLite-safe daily date extraction path for `/api/reports`.

## 3. Frontend
- [x] 3.1 Allow nullable `accountId` in frontend reports schema.
- [x] 3.2 Expose account and model controls in the reports filter bar.
- [x] 3.3 Show center totals in the model and user-agent distribution donuts.
- [x] 3.4 Compact-format report donut totals and legend values for cost and request metrics.

## 4. Verification
- [x] 4.1 Run focused integration test for `/api/reports` with SQLite date aggregation.
- [x] 4.2 Run `ruff check` on report modules.
- [x] 4.3 Run `ty` check for report modules.
- [x] 4.4 Run focused frontend report donut tests.
