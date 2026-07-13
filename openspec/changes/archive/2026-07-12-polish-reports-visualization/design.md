## Context

The current reports surface already has the right component boundaries for this work: `ReportsPage` owns the filter state and request wiring, `ReportsFilters` renders the controls, `ReportsSummaryCards` renders the top-level metrics, `DailyDetailTable` renders the tabular daily output, and the charts stay isolated in small presentational components. The consolidation is therefore a behavior and contract cleanup inside the existing reports stack rather than a new architecture.

Two earlier OpenSpec changes captured narrower parts of this behavior and one of them now contradicts the implemented reports UI by keeping the last-clicked preset highlighted after manual date edits. This consolidated change replaces those fragments with one coherent `/reports` contract.

## Goals / Non-Goals

**Goals:**

- Make this change the single surviving OpenSpec record for the covered reports work.
- Keep report date filters aligned with the browser's local calendar and prevent future-date input.
- Send browser-local timezone context on reports requests and make the endpoint interpret ranges and daily buckets in that timezone.
- Show previous-window summary-card comparison only when the immediately preceding window is fully covered.
- Show a continuous daily breakdown window by filling missing calendar days with zero-valued rows.
- Keep daily breakdown dates in ISO `yyyy-mm-dd` format.
- Keep the daily breakdown header fixed while only the body scrolls, with a default height of seven rows.
- Clear quick-preset highlighting as soon as the operator manually edits the date range.
- Keep the model donut driven by cost data without rendering a center value.
- Balance the left and right chart padding for the two daily reports charts.
- Keep the change local to the existing reports frontend/backend contracts and tests.

**Non-Goals:**

- Adding a persisted user-configurable reporting timezone.
- Recomputing whether a custom range should automatically reselect `7d`, `30d`, or `90d`.
- Changing CSV export columns.
- Redesigning the overall reports layout.

## Decisions

### 1. Keep preset state in `ReportsPage`, but clear it on manual date edits

`ReportsPage` already owns the canonical filter state, so it will continue to own `selectedPresetDays`. Preset button clicks will set both the date range and the selected preset. Manual changes to `startDate` or `endDate` will set `selectedPresetDays` to `null` before updating the filters.

The date inputs also use the browser-local current day as their `max` value so operators cannot pick a future report date from the native control.

### 2. Send browser-local timezone on each reports request

The reports page will detect the browser's current IANA timezone, cache the latest valid value locally for convenience, and include a valid timezone on `GET /api/reports` whenever one is available. The fallback order is: use the browser's live detected timezone when it is valid, otherwise reuse the cached valid timezone, otherwise omit the `timezone` query parameter entirely. The backend will interpret `start_date` and `end_date` as local-midnight boundaries in the supplied timezone, convert those boundaries to UTC for filtering, and bucket `daily` rows by that same local calendar day. Missing or invalid request timezones fall back to UTC.

### 3. Expose previous-window comparison conservatively

The reports response includes a `comparison` block with `canCompare` plus previous-window totals for cost, tokens, and requests. The backend only sets `canCompare` when the earliest eligible report activity proves the immediately preceding window is fully covered. The summary cards render percentage deltas only when `canCompare` is true and the relevant previous total is greater than zero.

### 4. Fill missing daily rows inside `DailyDetailTable`

The daily table will build a contiguous calendar window from the active start and end dates, map API rows by date, and synthesize zero-valued rows for any missing day.

### 5. Split the daily table into a fixed header and scrollable body

The table rendering will be structured so the header remains visible and only the body scrolls. The body height will be capped to approximately seven row heights.

### 6. Keep the donut legend cost-based without center text

`ModelDistributionDonut` continues to size slices from `costUsd` and show cost in the legend and tooltip, but it does not render a center label or hover-driven center text.

### 7. Normalize chart margins locally in each chart component

`CostPerDayChart` and `TokensPerDayChart` will each update their chart margin config so `left` and `right` are equal.

## Verification

- Validate that manual date edits clear preset highlighting and that report date inputs reject future dates.
- Add or keep coverage for timezone detection, cached timezone reuse, invalid-live-detection fallback, and `timezone` request wiring.
- Validate reports summary-card comparison rendering for available and suppressed previous-window cases.
- Add backend coverage for timezone-aware range interpretation, local-day daily bucketing, and UTC fallback.
- Add daily table coverage for zero-filled missing days and the seven-row scroll container.
- Validate the donut stays cost-based without a center value.
- Verify the two daily chart components use equal left and right margins.
- Run `uv run openspec validate --specs`.
