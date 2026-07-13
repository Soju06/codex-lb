## 1. OpenSpec Consolidation

- [x] 1.1 Rewrite `polish-reports-visualization` proposal, design, tasks, and the `frontend-architecture` delta so they describe the final consolidated reports behavior.
- [x] 1.2 Absorb the selected scope from `polish-report-filter-presets` and `respect-browser-reports-timezone`, then delete those change folders.

## 2. Reports Filters And Requests

- [x] 2.1 Keep visible `/reports` controls for presets, start date, end date, account, and model.
- [x] 2.2 Clear `selectedPresetDays` after manual start/end edits while preset clicks still set the active range.
- [x] 2.3 Prevent future dates in the report start and end inputs.
- [x] 2.4 Detect the browser's IANA timezone, cache the latest valid value locally, and include it on reports requests when available.

## 3. Reports Backend And Summary Cards

- [x] 3.1 Make `GET /api/reports` interpret `start_date` and `end_date` as local-midnight boundaries in the requested timezone, falling back to UTC when the timezone is missing or invalid.
- [x] 3.2 Bucket `daily` report rows by calendar day in the requested timezone.
- [x] 3.3 Expose a previous-window comparison block for report summary cards and gate `canCompare` on full previous-window coverage.
- [x] 3.4 Render report summary-card percentage deltas only for `Total Cost`, `Tokens`, and `Requests` when comparison is available.

## 4. Reports Visualization

- [x] 4.1 Render the daily breakdown as a continuous ISO calendar window with zero-filled missing days.
- [x] 4.2 Keep the daily breakdown header visible while only the body scrolls through a seven-row viewport.
- [x] 4.3 Keep the model distribution donut cost-based while omitting any center value.
- [x] 4.4 Use equal left and right plot padding in the `Cost by Day` and `Tokens by Day` charts.

## 5. Verification

- [x] 5.1 Keep or add focused frontend coverage for preset clearing, future-date inputs, timezone request wiring, summary-card comparison rendering, daily zero-fill behavior, donut rendering, and chart padding.
- [x] 5.2 Keep or add focused backend coverage for timezone-aware report ranges, local-day daily bucketing, previous-window comparison gating, and UTC fallback.
- [x] 5.3 Run `uv run openspec validate --specs`.
