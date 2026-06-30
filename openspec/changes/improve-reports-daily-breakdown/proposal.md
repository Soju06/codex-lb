## Why

The `/reports` page currently renders chart series directly from the API daily rows while the Daily Breakdown table fills missing selected dates locally. When the API omits a zero-usage day, the charts show a gap in the selected range that does not match the table.

Operators also need the Daily Breakdown table to be easier to inspect: the visible columns are not sortable, the default ordering is not newest-first, cached input tokens are only available in CSV output instead of the rendered Input Tokens column, the Tokens summary subtitle omits cached-token totals, CSV export order follows the current table sort instead of a stable chronological order, and sortable headers do not expose visible sort state.

## What Changes

- Fill missing `/reports` daily rows with zero-valued records across the full selected date range before rendering the cost and token charts.
- Reuse the same continuous daily-row normalization for the Daily Breakdown table so the reports page uses one consistent daily series.
- Add sortable headers to every visible Daily Breakdown column and default the table to sorting `Day` in descending order.
- Render cached input tokens inline in the Daily Breakdown `Input Tokens` column as muted secondary text in `main (cached)` format, including `0 (0)` when no input or cached tokens are present.
- Add cached totals to the `/reports` Tokens summary subtitle in `Input ... · Cache ... · Output ...` format.
- Export Daily Breakdown CSV rows in ascending `Day` order regardless of the visible table sort state.
- Show a muted unsorted icon on inactive sortable headers and a directional active icon on the current sorted header.
- Keep `/reports` data loading on `GET /api/reports` and avoid API or schema changes.

## Capabilities

### New Capabilities

### Modified Capabilities

- `frontend-architecture`: `/reports` daily charts and the Daily Breakdown table now define continuous selected-range day filling, explicit table sorting behavior, visible sort indicators, stable chronological CSV export, cached-token rendering within the Input Tokens column, and cached-token totals in the Tokens summary subtitle.

## Impact

- Frontend: `frontend/src/features/reports/components/*` daily charts, summary cards, and table rendering, plus a shared reports daily-series helper.
- Tests: reports chart, summary-card, and table component tests covering missing-day fill, default sort order, visible sort indicators, chronological CSV export, and cached-token rendering.
- Specs: `frontend-architecture` delta for reports chart continuity and `/reports` summary/table interaction and presentation behavior.
