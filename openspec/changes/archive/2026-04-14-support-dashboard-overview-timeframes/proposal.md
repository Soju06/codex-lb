## Why

The dashboard overview currently hard-codes a 7-day activity horizon for requests, tokens, cost, and error rate. That makes the page good at one question only: "what happened this week?" Operators cannot quickly switch to a short horizon to see what changed today or a longer horizon to understand monthly cost and stability trends.

The current overview contract also bakes the 7-day assumption into field names such as `requests7d`, `errorRate7d`, and `totalUsd7d`. Those names become misleading as soon as the overview supports any other timeframe. Extending the feature without first fixing the contract would preserve ambiguity and create avoidable frontend/backend cleanup later.

## What Changes

- Add an overview timeframe selector for the dashboard summary cards and trend charts with supported values `1d`, `7d`, and `30d`.
- Make `GET /api/dashboard/overview` accept an explicit `timeframe` parameter and return matching timeframe metadata in the response.
- Replace the current 7-day-specific overview aggregate fields with window-neutral names and remove the legacy `...7d` response fields.
- Keep primary and secondary quota donuts plus depletion indicators tied to their actual quota windows rather than the selected overview timeframe.
- Define stable trend density per timeframe so the frontend can rely on a predictable chart shape.

## Impact

- Specs: `openspec/specs/frontend-architecture/spec.md`
- Backend: dashboard API parameter validation, overview service aggregation, response schemas, trend builders
- Frontend: dashboard header controls, overview query keying, stats/trend rendering, mocks, screenshot fixtures
- Tests: backend integration/unit coverage for timeframe-aware overview responses, frontend schema/hook/view coverage for selector behavior and the clean response contract
- Compatibility: BREAKING change for the dashboard overview response; legacy `requests7d`, `errorRate7d`, `tokensSecondaryWindow`, `cachedTokensSecondaryWindow`, and `totalUsd7d` fields will be removed rather than retained in parallel
