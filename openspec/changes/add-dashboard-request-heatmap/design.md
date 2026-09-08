## Context

The dashboard already has a weekly credits pace card in the right-hand slot beside the usage donuts. Dashboard preferences are intentionally browser-local, while request usage is already maintained in hourly rollups with a bounded raw tail reader. The heatmap must use the browser's requested IANA timezone without turning that preference into server state.

## Decisions

- Add a `dashboardDisplayMode` local preference with `weeklyPace` as the default and `requestHeatmap` as the alternate value.
- Keep the existing right-hand slot and render exactly one of the two views. When heatmap mode is active, the usage donuts remain unchanged.
- Add a dedicated `GET /api/dashboard/request-activity` endpoint rather than expanding the overview response. The endpoint accepts an optional IANA timezone and falls back to UTC when it is absent or invalid. The request is enabled only for heatmap mode, so the normal dashboard path does not perform another query.
- Capture the current instant once per request in the effective timezone and define the display period as local calendar dates from the first day of the month five months before the current local month through the current local date. For completed dates, convert each local midnight and following local midnight independently to UTC; for the current local date, convert its local midnight independently and use the captured instant as the exclusive UTC end instead of tomorrow's midnight. This prevents future time from entering the current-day bucket while preserving DST semantics.
- Build the folded path as one watermark-consistent `AccountUsageRollupState LEFT JOIN` rollup SQL statement. It creates bounded labeled local-day ranges, obtains the fold watermark, and groups whole folded UTC-hour `request_count` values by local-day label in SQL. Read the exact complement of those whole folded buckets through separate bounded raw SQL queries, including the post-watermark portion and any pre-watermark partial edge windows caused by non-hour-aligned local midnights, then merge only the small per-day totals in application code; no full-grain hourly or raw rows are materialized.
- Render the heatmap using CSS grid cells and the existing Radix tooltip primitives. This avoids a chart dependency and allows the tooltip and cell colors to use existing theme variables.
- Return only non-empty daily buckets from the API. The frontend fills the complete six-month calendar range so zero-request days remain visible.
- Refresh the client query on a fixed interval of at least 60 seconds, and include the browser IANA timezone in both the request and query/cache key. Build frontend calendar labels and cells from browser-local date components rather than UTC getters.

## Data Path And Scan Review

The new endpoint will reuse the existing watermark-aware hourly data, but the aggregation boundary is the requested timezone's local day rather than UTC midnight. The folded SQL statement should obtain the watermark from `AccountUsageRollupState` and `LEFT JOIN` the hourly rollup source, represent each completed local day with its independently converted UTC start and following-midnight end, represent the current local day with its independently converted UTC start and the captured current instant as its end, constrain folded rows by the hourly rollup time-bucket key range, and group whole folded UTC-hour buckets by the bounded local-day label. Raw reads must cover exactly the requested UTC segments not represented by those whole folded buckets: the bounded post-watermark segment and any bounded pre-watermark partial edge windows where a local-day boundary falls between UTC hour boundaries. Those raw queries group by the same local-day label in SQL. Application code merges the resulting small per-day maps. This preserves watermark consistency without per-grain materialization and avoids an unbounded `request_logs` query or per-day application-side table scan.

Hourly retention creates a deliberate limitation for timezones whose local midnight is not aligned to a UTC hour (for example, a 30- or 45-minute offset). Once an hour has been folded and raw detail for a pre-watermark partial edge window is unavailable, the original partial-hour contribution cannot be reconstructed. The endpoint may therefore omit or undercount that unreconstructable partial contribution; it must not claim or assign the whole folded UTC-hour bucket merely because the edge detail was pruned. The endpoint tolerates that limitation and does not defeat the bounded query by recovering old raw rows.

## Testing

- Unit-test preference defaults, persistence, and invalid values.
- Test the dashboard mode switch and that only the selected card is rendered.
- Test heatmap calendar construction from browser-local date components, levels, and tooltip content, including the six-calendar-month unlabeled layout and a fixed refresh interval.
- Test timezone fallback, DST-crossing local-day bounds, captured-current-instant upper bounds without future time, browser timezone query/cache identity, and sparse responses.
- Test the repository/service aggregation over folded and raw-tail data, including the one watermark-consistent `AccountUsageRollupState LEFT JOIN` rollup statement, exact raw-complement bounds with pre-watermark partial edge windows, local-day grouping, per-day application merge, warmup exclusion, empty days, and the documented retention-pruned partial-hour undercount limitation.
- Validate the OpenSpec artifacts, frontend typecheck, focused Vitest suites, and the relevant Python tests.
