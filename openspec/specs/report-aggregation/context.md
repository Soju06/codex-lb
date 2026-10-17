Reports use permanent UTC-hour aggregates assembled into the requested timezone's days, plus the unfurled raw complement. The storage grain retains account, API key, model, User-Agent and normalized conversation dimensions so filtering and distinct counts remain valid after retention. The report fold and account lifecycle mirrors share the existing fold-state lock.

The report watermark starts at epoch and advances in paced six-hour slices, at most eight per existing 15-minute scheduler tick. Retention waits for every watermark, including reports. Backfill covers surviving raw data only; do not drop the aggregate once raw pruning has made it the only copy. Partial-hour timezone boundaries still require raw edge data, matching the existing hourly statistics limitation.

A 90-day report returns additive totals and exact conversation counts without raw speed median calculations. Up to seven days retains the existing exact speed query. The UI discloses omitted speed metrics and shows the server generation time. Server snapshots expire after 60 seconds; browser queries do not poll and offer explicit refresh.

For measured performance, regression evidence, screenshots and rollout constraints, see the [verified implementation record](../../changes/archive/2026-09-09-harden-reports-performance/context.md).
