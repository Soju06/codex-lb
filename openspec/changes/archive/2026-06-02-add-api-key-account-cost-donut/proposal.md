## Why

The APIs tab already exposes a 7-day usage trend for each API key, but operators could not see which accounts generated that cost without inspecting raw request logs. The implemented change adds an account-cost breakdown beside the existing trend and extends the API-key usage payload so the frontend can render that breakdown directly.

## What Changes

- Extend `GET /api/api-keys/{key_id}/usage-7d` with `accountCosts[]` entries that aggregate 7-day request-log cost by account.
- Preserve deleted-account historical cost as a separate `Deleted Account` bucket instead of dropping it from the API-key detail view.
- Add an APIs-tab donut card that shows the 7-day account-cost breakdown beside the usage trend.
- Move the usage-trend Tokens/Cost legend below the accumulated toggle and tighten the chart right margin for the split layout.
- Add a request-log index optimized for API-key account-cost lookups over the 7-day window.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: API-key 7-day usage now includes per-account cost breakdown data and requires an index for that aggregation path.
- `frontend-architecture`: The APIs tab detail panel now includes a 7-day account-cost donut and updated trend-card layout behavior.

## Impact

- Backend: `app/modules/api_keys/{repository,service,api,schemas}.py`
- Database: `request_logs` index coverage for API-key/time/account aggregation
- Frontend: `frontend/src/features/apis/components/*` and `frontend/src/features/apis/schemas.ts`
- Tests: API-key usage integration tests, repository unit tests, and APIs-tab component tests
