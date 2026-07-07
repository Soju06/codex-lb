## 1. Settings contract

- [x] 1.1 Persist `weekly_pace_smoothing_minutes` with a default of 30 minutes.
- [x] 1.2 Validate settings updates to the allowed smoothing windows: 15, 30, 60, 120, and 240 minutes.
- [x] 1.3 Expose the setting through the backend settings response and frontend settings schema.

## 2. Weekly pace calculation

- [x] 2.1 Compute smoothed pace gap fields from recent weekly usage samples.
- [x] 2.2 Keep live `actualUsedPercent`, `deltaPercent`, and `scheduleGapCredits` available for instantaneous state.
- [x] 2.3 Exclude samples from older weekly reset/window segments when smoothing.

## 3. Dashboard

- [x] 3.1 Add a Routing settings control for the smoothing window.
- [x] 3.2 Render the Weekly credits pace card gap and gap-based recommendations from smoothed fields when present.

## 4. Verification

- [x] 4.1 Add backend settings, migration, and weekly pace smoothing regression tests.
- [x] 4.2 Add frontend settings schema, settings control, and pace card tests.
- [x] 4.3 Run focused backend/frontend tests, lint, and migration check.
