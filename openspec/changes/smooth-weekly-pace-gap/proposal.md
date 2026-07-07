## Why

The Weekly credits pace card reports the current schedule gap from the latest usage sample, so a short burst can make the dashboard look far over plan even when the recent trend is not sustained. Operators need a bounded rolling average for the displayed pace gap without hiding the live "Used now" value.

## What Changes

- Add a persisted dashboard setting for the weekly pace gap smoothing window with allowed values 15m, 30m, 1h, 2h, and 4h.
- Compute smoothed weekly pace gap fields from recent weekly usage samples and expose them in the dashboard projections payload.
- Keep smoothing within the current weekly quota reset/window segment so pre-reset samples do not leak into a just-reset window.
- Surface the smoothing setting in Routing settings and display the smoothed gap on the Weekly credits pace card.

## Impact

- Dashboard settings API, repository/service/model, and Alembic migration.
- Weekly credits pace projection calculation and response schema.
- Dashboard settings UI and weekly pace card rendering.
