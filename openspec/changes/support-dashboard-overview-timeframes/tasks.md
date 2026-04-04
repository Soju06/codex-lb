## 1. Spec

- [x] 1.1 Modify `frontend-architecture` so the dashboard overview supports selectable `1d`, `7d`, and `30d` activity horizons
- [x] 1.2 Lock the clean overview response contract with explicit `timeframe` metadata and window-neutral aggregate fields
- [x] 1.3 Specify that overview timeframe changes are independent from request-log filters and pagination state
- [x] 1.4 Specify that quota donuts and depletion indicators remain tied to actual quota windows

## 2. Backend

- [x] 2.1 Add strict `timeframe` query validation to `GET /api/dashboard/overview` with default `7d`
- [x] 2.2 Replace fixed 7-day overview aggregation with timeframe-aware lookback and bucket configuration
- [x] 2.3 Update dashboard and usage schemas/builders to emit `timeframe`, `summary.cost.totalUsd`, `summary.metrics.requests`, `summary.metrics.tokens`, `summary.metrics.cachedInputTokens`, `summary.metrics.errorRate`, and `summary.metrics.errorCount`
- [x] 2.4 Remove legacy overview response fields with 7-day-specific names
- [x] 2.5 Keep primary/secondary quota window summaries and depletion calculations unchanged

## 3. Frontend

- [x] 3.1 Add a dedicated overview timeframe selector in the dashboard header
- [x] 3.2 Update the overview query hook and query keys to include timeframe without coupling to request-log filters
- [x] 3.3 Update stats cards and trend rendering to use the new neutral response fields and response-provided timeframe metadata
- [x] 3.4 Preserve the existing request-log timeframe selector as a request-log-only control

## 4. Tests

- [x] 4.1 Add backend tests covering `1d`, `7d`, and `30d` overview responses, including trend bucket counts
- [x] 4.2 Update backend tests to assert removal of legacy `...7d` overview fields
- [x] 4.3 Update frontend schema, hook, and integration tests for the new overview response and selector behavior
- [x] 4.4 Update frontend mocks and screenshot fixtures to match the new contract
