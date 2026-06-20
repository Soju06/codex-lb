## Context

`DailyDetailTable` currently owns the only continuous-day fill logic on `/reports`. It builds zero-valued rows for missing dates between `startDate` and `endDate`, but `CostPerDayChart` and `TokensPerDayChart` still map the raw API payload directly. The result is one selected-range representation in the table and another in the charts.

The table also renders fixed headers and row order without any client-side sorting state. Cached input tokens are already part of each `DailyReportRow` and are exported in the CSV, but they are not surfaced in the visible table cells.

## Goals / Non-Goals

**Goals:**

- Use one normalized daily series for the reports charts and Daily Breakdown table.
- Make every visible Daily Breakdown column sortable while preserving a predictable default order.
- Surface cached input tokens inline in the Input Tokens column without adding a new visible column.
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

### 2. Keep sorting local to `DailyDetailTable`

The Daily Breakdown table will own a local sort key and direction, defaulted to `date desc`, and will derive sorted rows from the normalized daily series before rendering and CSV export.

Rationale:

- Sorting is a table-only interaction and should not affect charts.
- Local state keeps the change contained and avoids broadening the reports page filter contract.
- Exporting the currently sorted rows matches what the operator sees.

Alternative considered:

- Store sorting in page-level state: rejected because no other reports surface consumes it.

### 3. Render cached tokens as muted secondary text within the Input Tokens cell

The Input Tokens cell will display the primary token total followed by cached input tokens in parentheses using smaller muted text, including zero values.

Rationale:

- This exposes already-available data without widening the table.
- Showing `0` explicitly avoids ambiguous blank cells for missing or zero cached tokens.
- A secondary visual treatment keeps the main token total dominant.

Alternative considered:

- Reintroduce a separate visible Cached Tokens column: rejected because the request explicitly places cached tokens inside the Input Tokens column and the current six-column layout is already compact.

## Risks / Trade-offs

- Shared date normalization could be reused incorrectly outside the selected-range reports flow. → Mitigation: keep the helper scoped inside `frontend/src/features/reports` and name it around reports daily rows.
- Client-side sorting after zero-fill means more synthetic rows participate in the sort order. → Mitigation: this matches the requested full-range behavior and keeps all columns sorting over the same rendered dataset.
- Muted cached-token text could be overlooked on dense tables. → Mitigation: keep the cached count explicit in parentheses and preserve it in CSV export.
