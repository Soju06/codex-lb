## Context

`DailyDetailTable` currently owns the only continuous-day fill logic on `/reports`. It builds zero-valued rows for missing dates between `startDate` and `endDate`, but `CostPerDayChart` and `TokensPerDayChart` still map the raw API payload directly. The result is one selected-range representation in the table and another in the charts.

The table also renders fixed headers and row order without any client-side sorting state. Cached input tokens are already part of each `DailyReportRow` and are exported in the CSV, but they are not surfaced in the visible table cells. The Tokens summary card also omits cached-token totals, and the sortable headers do not expose visible sort state.

## Goals / Non-Goals

**Goals:**

- Use one normalized daily series for the reports charts and Daily Breakdown table.
- Make every visible Daily Breakdown column sortable while preserving a predictable default order.
- Surface cached input tokens inline in the Input Tokens column without adding a new visible column.
- Surface cached token totals in the `/reports` Tokens summary subtitle.
- Keep Daily Breakdown CSV export in ascending chronological day order regardless of interactive table sorting.
- Show visible inactive and active sort indicators on sortable Daily Breakdown headers.
- Cover the new reports behavior with focused component tests.

**Non-Goals:**

- Changing the `/api/reports` response shape or adding new backend fields.
- Adding persisted sort preferences, server-side sorting, or new report filters.
- Changing CSV columns beyond continuing to export the existing cached-token field.

## Decisions

### 1. Extract continuous selected-range row building into a shared reports helper

The existing `DailyDetailTable` date-fill logic will move into a shared reports utility so both charts and the table consume the same normalized `DailyReportRow[]`.

Rationale:

- This is the smallest way to remove chart/table drift.
- The date-fill behavior is already accepted on the table, so reusing it avoids inventing a second continuity rule for charts.
- Keeping the helper in the reports feature preserves the behavior as UI shaping instead of moving it into generic query code.

Alternative considered:

- Normalize rows independently inside each chart: rejected because it duplicates date-range logic and invites future drift.

### 2. Keep sorting local to `DailyDetailTable` while exporting chronological CSV rows

The Daily Breakdown table will own a local sort key and direction, defaulted to `date desc`, and will derive sorted rows from the normalized daily series before rendering. CSV export will sort a separate copy of the rendered-day rows by `date asc` immediately before file generation.

Rationale:

- Sorting is a table-only interaction and should not affect charts.
- Local state keeps the change contained and avoids broadening the reports page filter contract.
- Exporting chronological rows gives operators a stable file order that does not depend on the last interactive table sort.

Alternative considered:

- Store sorting in page-level state: rejected because no other reports surface consumes it.
- Export the currently visible table order: rejected because the request explicitly requires ascending `Day` order regardless of the current table sort.

### 3. Render cached tokens in the summary subtitle and table without adding new columns

The Tokens summary card subtitle will display `Input`, `Cache`, and `Output` totals in that order. The Daily Breakdown Input Tokens cell will continue to display the primary token total followed by cached input tokens in parentheses using smaller muted text, including zero values.

Rationale:

- This exposes already-available cache data in both the top-level summary and row-level table without changing the API shape.
- The summary subtitle keeps all token totals in one line with the existing compact card layout.
- Showing `0` explicitly avoids ambiguous blank cells for missing or zero cached tokens.

Alternative considered:

- Add a separate cached-token summary card or visible table column: rejected because the request is a presentation refinement within the existing cards and six-column table.

### 4. Show visible sort-state icons on sortable headers

Each sortable Daily Breakdown header will render an icon even when inactive. Inactive columns will show a muted gray unsorted indicator, and the active sorted column will show the current ascending or descending direction in the same bright foreground treatment as the active header label.

Rationale:

- The current headers are clickable but visually silent, so a persistent affordance makes sorting discoverable.
- Keeping the inactive icon muted preserves emphasis on the active column while still signaling interactivity.
- Direction-specific active icons let operators confirm the current sort state without re-reading row values.

Alternative considered:

- Show icons only on the active column: rejected because the user explicitly wants a no-sort indicator on unsorted columns.

## Risks / Trade-offs

- Shared date normalization could be reused incorrectly outside the selected-range reports flow. → Mitigation: keep the helper scoped inside `frontend/src/features/reports` and name it around reports daily rows.
- Client-side sorting after zero-fill means more synthetic rows participate in the sort order. → Mitigation: this matches the requested full-range behavior and keeps all columns sorting over the same rendered dataset.
- Muted cached-token text could be overlooked on dense tables. → Mitigation: keep the cached count explicit in parentheses and preserve it in CSV export.
- Sort icons could add visual noise in dense headers. → Mitigation: use the existing muted/foreground contrast so inactive columns stay low-emphasis while the active one remains clear.
