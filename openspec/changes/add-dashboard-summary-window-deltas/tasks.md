## 1. Backend comparison aggregation

- [x] 1.1 Add bounded request-log aggregation support for eligible dashboard summary rows so the service can compute current-window and previous-window totals with the same query semantics.
- [x] 1.2 Add earliest eligible request-log coverage lookup so the dashboard service can determine whether the previous same-length window is fully covered.
- [x] 1.3 Update `DashboardService.get_overview()` to compute previous-window requests, tokens, and estimated API cost totals for `1d`, `7d`, and `30d`, and suppress comparison when coverage is incomplete.

## 2. Overview API and frontend schema wiring

- [x] 2.1 Extend backend dashboard overview schemas to expose an optional comparison block with `canCompare` and previous totals for requests, tokens, and cost.
- [x] 2.2 Extend frontend dashboard zod schemas and types to parse the new overview comparison block without breaking older responses that omit it.
- [x] 2.3 Update dashboard view-model helpers to derive per-card comparison display data only for `Requests`, `Tokens`, and `Est. API Cost`.

## 3. Dashboard summary card rendering

- [x] 3.1 Extend the stats-grid card data model to carry an optional comparison indicator alongside existing value and meta text.
- [x] 3.2 Render compact previous-window percentage indicators on the `Requests`, `Tokens`, and `Est. API Cost` cards using existing positive `emerald` and negative `red` semantics.
- [x] 3.3 Keep `Error rate` and `Account burn projection` unchanged, and hide comparison indicators when `canCompare` is false or the previous total is zero or missing.

## 4. Regression coverage and validation

- [x] 4.1 Add backend tests covering previous-window totals, incomplete previous-window suppression, and zero-or-missing previous totals.
- [x] 4.2 Add frontend tests covering schema parsing and stat-card rendering for increase, decrease, and hidden-comparison states.
- [x] 4.3 Run targeted frontend and backend test commands plus `openspec validate add-dashboard-summary-window-deltas --strict` and record any follow-up fixes needed before implementation is considered complete.
