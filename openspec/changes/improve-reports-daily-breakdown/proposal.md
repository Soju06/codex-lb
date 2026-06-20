## Why

The `/reports` page currently renders chart series directly from the API daily rows while the Daily Breakdown table fills missing selected dates locally. When the API omits a zero-usage day, the charts show a gap in the selected range that does not match the table.

Operators also need the Daily Breakdown table to be easier to inspect: the visible columns are not sortable, the default ordering is not newest-first, and cached input tokens are only available in CSV output instead of the rendered Input Tokens column.

## What Changes

- Fill missing `/reports` daily rows with zero-valued records across the full selected date range before rendering the cost and token charts.
- Reuse the same continuous daily-row normalization for the Daily Breakdown table so the reports page uses one consistent daily series.
- Add sortable headers to every visible Daily Breakdown column and default the table to sorting `Day` in descending order.
- Render cached input tokens inline in the Daily Breakdown `Input Tokens` column as muted secondary text in `main (cached)` format, including `0 (0)` when no input or cached tokens are present.
- Keep `/reports` data loading on `GET /api/reports` and avoid API or schema changes.

## Capabilities

### New Capabilities

### Modified Capabilities

- `frontend-architecture`: `/reports` daily charts and the Daily Breakdown table now define continuous selected-range day filling, explicit table sorting behavior, and cached-token rendering within the Input Tokens column.

## Impact

- Frontend: `frontend/src/features/reports/components/*` daily charts and table rendering, plus a shared reports daily-series helper.
- Tests: reports chart and table component tests covering missing-day fill, default sort order, sortable headers, and cached-token rendering.
- Specs: `frontend-architecture` delta for reports chart continuity and Daily Breakdown interaction/presentation behavior.
