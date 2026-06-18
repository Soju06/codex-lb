## Why

The dashboard overview shows current-window totals for Requests, Tokens, and Est. API Cost, but operators cannot quickly tell whether usage is rising or falling relative to the immediately preceding equivalent window. Adding a previous-window percentage delta makes the existing summary cards more actionable while keeping the comparison aligned with the selected `1d`, `7d`, or `30d` timeframe.

## What Changes

- Extend the dashboard overview contract so the summary metrics include previous-window totals for requests, tokens, and estimated API cost.
- Define when previous-window comparison is allowed: only when the preceding window is fully covered by available request-log history for the selected timeframe.
- Update the dashboard summary cards to render percentage-change indicators for Requests, Tokens, and Est. API Cost, using the project's established positive/negative color semantics and hiding the indicator when comparison is unavailable.
- Leave Error rate and Account burn projection unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: The dashboard overview requirements will expand to cover previous-window comparison data and rendering rules for the existing Requests, Tokens, and Est. API Cost cards.

## Impact

- Backend overview aggregation in `app/modules/dashboard/` and request-log aggregation in `app/modules/request_logs/`
- Dashboard overview API response for `GET /api/dashboard/overview`
- Frontend dashboard schemas, view-model building, and summary-card rendering in `frontend/src/features/dashboard/`
- Backend and frontend tests covering previous-window completeness and delta rendering
