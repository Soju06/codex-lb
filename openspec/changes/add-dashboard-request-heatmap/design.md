## Context

The dashboard already has a weekly credits pace card in the right-hand slot beside the usage donuts. Dashboard preferences are intentionally browser-local, while request usage is already maintained in hourly rollups with a bounded raw tail reader.

## Decisions

- Add a `dashboardDisplayMode` local preference with `weeklyPace` as the default and `requestHeatmap` as the alternate value.
- Keep the existing right-hand slot and render exactly one of the two views. When heatmap mode is active, the usage donuts remain unchanged.
- Add a dedicated `GET /api/dashboard/request-activity` endpoint rather than expanding the overview response. The request is enabled only for heatmap mode, so the normal dashboard path does not perform another query.
- Aggregate request counts by UTC day from `request_usage_hourly_rollups` plus only the un-folded raw tail. The rollup query leads with the primary-key time bucket range; the raw fallback is bounded by the existing `requested_at` index path.
- Render the heatmap using CSS grid cells and the existing Radix tooltip primitives. This avoids a chart dependency and allows the tooltip and cell colors to use existing theme variables.
- Return only non-empty daily buckets from the API. The frontend fills the complete six-month calendar range so zero-request days remain visible.

## Data Path And Scan Review

The new endpoint will reuse the existing watermark-aware hourly reader. Folded rows are read by `bucket_epoch` range from the hourly rollup primary key, and raw rows are read only for the tail after the fold watermark with a `requested_at` range. The raw tail query groups by UTC day and excludes warmup traffic. No unbounded `request_logs` query or per-day application-side table scan is introduced.

## Testing

- Unit-test preference defaults, persistence, and invalid values.
- Test the dashboard mode switch and that only the selected card is rendered.
- Test heatmap calendar construction, levels, and tooltip content, including the six-month unlabeled layout.
- Test the repository/service aggregation over folded and raw-tail data, including warmup exclusion and empty days.
- Validate the OpenSpec artifacts, frontend typecheck, focused Vitest suites, and the relevant Python tests.
