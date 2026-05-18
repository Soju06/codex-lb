## 1. Contract

- [x] 1.1 Add backend dashboard `weeklyCreditPace` response schema fields for schedule gap, recent-rate forecast, freshness exclusions, and confidence.
- [x] 1.2 Add frontend zod/type support for the optional server-provided pace object.

## 2. Calculation

- [x] 2.1 Compute weekly pace server-side from active accounts only.
- [x] 2.2 Exclude weekly usage samples whose latest `recorded_at` is stale relative to the configured usage refresh interval.
- [x] 2.3 Compute current linear schedule gap separately from projected shortfall.
- [x] 2.4 Use recent usage history/EWMA for forecast burn instead of full-window cumulative average.

## 3. UI

- [x] 3.1 Prefer backend `weeklyCreditPace` when present and keep a frontend fallback for older payloads.
- [x] 3.2 Update card labels so current schedule gap and future forecast are not conflated.

## 4. Verification

- [x] 4.1 Backend integration tests cover inactive/stale exclusion and recent low-burn forecast.
- [x] 4.2 Frontend unit/component tests cover backend pace preference and updated labels.
- [x] 4.3 Run targeted backend and frontend tests.
- [x] 4.4 Run local lint/type/spec validation where available.
