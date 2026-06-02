## Context

The existing APIs tab already had two independent data sources for a selected API key: a trend query and a 7-day usage summary query. The implemented change builds on the 7-day usage query instead of introducing a third endpoint, because the donut only needs aggregate 7-day cost totals and should stay aligned with the summary card totals already shown in the detail panel.

This change spans backend aggregation, API schema updates, frontend rendering, and a query-performance migration. It also has one behavior detail that differs from the original request text: the deleted-account bucket is currently sorted by descending cost with all other buckets rather than being pinned to the final slice. The backfilled OpenSpec needs to match the implemented behavior so verification remains truthful.

## Goals / Non-Goals

**Goals:**

- Expose a single 7-day API-key usage payload that includes both total usage and per-account cost breakdown.
- Preserve historical deleted-account cost in the API-key detail view.
- Reuse the dashboard donut visual language so the APIs tab feels consistent.
- Keep the account-cost query indexable for the 7-day filtered API-key lookup path.

**Non-Goals:**

- Do not add a new dedicated account-cost endpoint.
- Do not replace the existing trend visualization.
- Do not introduce account display-name fields beyond the currently available email-based label.
- Do not retrofit unimplemented behavior such as forcing the deleted-account slice to the end regardless of cost.

## Decisions

### Reuse `usage-7d` instead of adding another API

The backend now computes total 7-day usage and grouped account costs together in `ApiKeysRepository.usage_7d(...)`. This keeps the donut totals consistent with the summary totals, avoids a second round-trip from the frontend, and allows the service/API layer to expose one stable response shape.

Alternative considered: a separate `usage-7d-by-account` endpoint. Rejected because it duplicates the same time-window filtering and creates coordination risk between summary and breakdown totals.

### Represent deleted-account cost as a synthetic grouped bucket

Rows with `request_logs.deleted_at IS NOT NULL` are folded into one synthetic bucket with `accountId: null`, `email: null`, and `isDeleted: true`. Unknown-but-not-deleted account usage stays separate as `isDeleted: false`.

Alternative considered: dropping deleted rows from the donut. Rejected because it would make the per-account donut total disagree with the 7-day summary total.

### Sort all account-cost buckets by descending cost

The repository sorts grouped buckets by `cost_usd DESC` after building the deleted-account synthetic bucket. This means the deleted-account bucket participates in the same descending order as all other buckets.

Alternative considered: always pin deleted usage to the final slice. Rejected in the implemented code path; the spec backfill preserves the shipped descending-cost behavior.

### Mirror the dashboard donut's sizing and motion rules with a focused component

The frontend uses a dedicated `AccountCostDonut` component that reuses the dashboard donut's sizing constants, palette generation, consumed gray color, center-value treatment, and reduced-motion animation behavior while simplifying features not needed in the APIs tab.

Alternative considered: directly reusing the generic dashboard donut component. Rejected because the APIs-tab donut needs currency formatting and a smaller fixed legend contract.

### Add a composite request-log index for the aggregation path

The migration adds `idx_logs_api_key_time_account` on `request_logs(api_key_id, requested_at DESC, account_id)` so the 7-day filtered grouping path does not degrade into a full table scan as request-log volume grows.

Alternative considered: relying on existing `api_key_id` or `requested_at` indexes independently. Rejected because the implemented query filters by API key and time range, then groups by account.

## Risks / Trade-offs

- [Spec vs request-text drift] -> The original request asked for deleted usage to render last, but the shipped code sorts by descending cost. The backfilled spec matches code to keep verification honest.
- [Label fidelity] -> The frontend legend currently uses `email` or `Unknown Account`, not a richer display-name field. This keeps the contract aligned with the existing payload.
- [UI duplication] -> The APIs-tab donut intentionally duplicates some dashboard donut behavior in a focused component. This avoids over-generalizing the generic donut at the cost of some repeated constants.
- [Query shape growth] -> The single-query `usage_7d` aggregation is more complex than a raw totals query. The added composite index mitigates the performance risk.
