## Context

The dashboard overview currently builds one current-window activity summary from `GET /api/dashboard/overview` and renders it through `frontend/src/features/dashboard/utils.ts` into the top stats grid. The selected overview timeframe already supports `1d`, `7d`, and `30d`, but the API only returns current-window totals and current-window trend buckets.

This change spans backend aggregation, API schema, frontend parsing, and stat-card rendering. The user requirement is specifically about comparing the current window against the immediately preceding equivalent window for the existing `Requests`, `Tokens`, and `Est. API Cost` cards. The existing `Error rate` and `Account burn projection` cards stay unchanged.

There is no persisted history-completeness marker in the current model. The only available proxy for whether the previous window fully exists is request-log coverage. Because the summary cards already exclude warmup rows, the comparison logic must use the same eligible request-log population.

## Goals / Non-Goals

**Goals:**

- Extend the dashboard overview response with enough data to compare current and previous windows for requests, tokens, and estimated API cost.
- Make the previous-window comparison honor the selected dashboard timeframe: `1d`, `7d`, or `30d`.
- Hide the comparison when the previous window is not fully covered by available eligible request-log history.
- Render compact up/down percentage indicators on the existing three summary cards using the project's established `emerald` positive styling and `red` negative styling.
- Keep the implementation local to the current dashboard overview path without adding a new endpoint.

**Non-Goals:**

- Changing the meaning, ordering, or visibility of the existing summary cards beyond the new comparison indicator.
- Adding comparison indicators to `Error rate` or `Account burn projection`.
- Reworking the dashboard trends payload or the sparkline charts.
- Introducing a historical backfill marker, migration, or new persistence model.

## Decisions

### 1. Extend the existing overview response instead of adding a new API

The comparison data will be added to the existing `summary` payload returned by `GET /api/dashboard/overview`.

Proposed shape:

```json
{
  "summary": {
    "metrics": {
      "requests": 1500,
      "tokens": 420000,
      "cachedInputTokens": 80000,
      "errorRate": 0.02,
      "errorCount": 30,
      "topError": "rate_limit_exceeded"
    },
    "cost": {
      "currency": "USD",
      "totalUsd": 12.34
    },
    "comparison": {
      "canCompare": true,
      "previous": {
        "requests": 1000,
        "tokens": 300000,
        "costUsd": 8.0
      }
    }
  }
}
```

Rationale:

- The dashboard already requests all top-card data from one overview query.
- A single payload keeps the comparison aligned with the same timeframe and refresh cycle as the current totals.
- This is the smallest schema change that can support the frontend without inventing a second request path.

Alternatives considered:

- Compute comparison entirely in the frontend from existing trends: rejected because the current API only exposes current-window buckets, not the previous-window totals or completeness signal.
- Add a new `/api/dashboard/comparison` endpoint: rejected because it adds request coordination and cache complexity for a narrow extension to the existing overview.

### 2. Use a backend range-based activity aggregate for both current and previous windows

The backend will introduce a range-based request-log aggregation helper that can summarize eligible rows inside `[since, until)` windows. `DashboardService.get_overview()` will compute:

- current window: `[now - timeframe.window_minutes, now]`
- previous window: `[now - 2 * timeframe.window_minutes, now - timeframe.window_minutes]`

The current summary metrics and previous summary metrics will both come from the same aggregation primitive so the counts and token totals stay consistent.

Rationale:

- The current implementation uses `aggregate_activity_since(since)` for the current window only.
- Previous-window comparison requires a bounded interval, not an open-ended lower bound.
- Using one bounded aggregation style for both windows avoids off-by-one behavior between the displayed current total and the comparison baseline.

Alternatives considered:

- Keep `aggregate_activity_since()` for current and add a separate previous-window helper: possible, but it increases the chance of drifting query semantics.

### 3. Determine comparison eligibility conservatively from earliest eligible request-log coverage

The API will only set `canCompare: true` when the request-log history proves the previous window is fully covered. The conservative rule is:

- find the earliest eligible request-log timestamp from the same population used by the summary cards
- if that timestamp is at or before `previous_window_start`, the previous window is considered fully covered
- otherwise, `canCompare` is false and the frontend renders no delta

Rationale:

- The user explicitly asked to suppress comparison when the previous window is shorter than the selected cycle, such as only 5 days of data for a 7-day window.
- The current system does not store a better completeness marker.
- A conservative rule avoids presenting a misleading percentage derived from partial history.

Trade-off:

- If the system had a full previous window but no requests during its early portion, this rule will still suppress the comparison because coverage cannot be proven from the available data. That is acceptable for this change because hiding the delta is safer than showing a potentially false one.

### 4. Return previous totals, but calculate displayed percent in the frontend

The backend will return previous raw totals and the `canCompare` flag. The frontend will calculate the displayed percentage for each of the three cards using a small helper in the dashboard feature.

Display rule:

- change percent = `((current - previous) / previous) * 100`
- positive result renders `▲ <rounded>%`
- negative result renders `▼ <rounded>%`
- zero result renders a neutral `0%` indicator or an unsigned compact label, depending on what matches the existing card layout most cleanly during implementation
- if `previous <= 0` or `canCompare` is false, render no indicator

Rationale:

- Raw previous totals are generally useful response data; arrow glyphs and display rounding are UI concerns.
- Keeping the percent formatting in the frontend lets the stat-card renderer control compact presentation.
- Hiding the indicator when `previous <= 0` avoids undefined or misleading percentage math.

Alternatives considered:

- Precompute percentage deltas in the backend: valid, but it couples API shape to card-specific presentation and still leaves the frontend deciding arrow, sign, and styling.

### 5. Reuse established success/failure text styles for the delta indicator

The delta indicator will use the same general semantic colors already present in the frontend:

- positive: `text-emerald-600 dark:text-emerald-400`
- negative: `text-red-600 dark:text-red-400`

The indicator will live inside the existing stat-card metadata area so the card layout remains compact and stable.

Rationale:

- These classes are already used in dashboard and related account surfaces for positive/negative state.
- Reusing them keeps the new indicator visually consistent with the rest of the product.

Alternatives considered:

- Introduce a custom comparison badge palette: rejected as unnecessary for a simple inline indicator.

### 6. Keep `top_error` and current trend data based on the current window only

Only the three top-card comparison metrics gain previous-window data. The existing `top_error` lookup and current-window sparkline trends remain unchanged.

Rationale:

- This preserves the current meaning of the non-comparison cards and avoids broadening the scope into comparative error analytics.
- The user explicitly limited the change to the three existing summary cards.

## Risks / Trade-offs

- [Partial-history detection is conservative] -> Use earliest eligible request-log coverage as an explicit safety gate and document that the UI hides comparison when full coverage cannot be proven.
- [Extra dashboard query work] -> Limit the backend change to two bounded aggregates plus one earliest-timestamp lookup, all against the same request-log dataset already queried for overview metrics.
- [Schema drift between backend and frontend] -> Add backend schema tests and frontend zod/schema tests for the new optional `summary.comparison` contract.
- [Percent display edge cases around zero] -> Treat `previous <= 0` as non-comparable and hide the indicator instead of inventing infinity or 100%-from-zero behavior.

## Migration Plan

No database migration is required.

Rollout plan:

1. Extend backend aggregation and response schemas with the optional comparison block.
2. Add frontend parsing and rendering for the three existing summary cards.
3. Verify that older states without comparison data degrade cleanly by rendering no indicator.

Rollback plan:

- Revert the comparison block population in the backend and the frontend indicator rendering. Because the new fields are additive and optional, rollback does not require data cleanup.

## Open Questions

None for the current scope. The design fixes the conservative completeness rule and the `previous <= 0` handling so implementation can proceed without another behavior decision.
