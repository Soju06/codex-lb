## Why

Weekly credit pacing currently assumes every day in a weekly quota window is equally available for scheduled usage. Operators who only want to spend weekly quota on business days need the dashboard schedule to ignore non-working days, otherwise weekend and weekday pace gaps are misleading.

## What Changes

- Add a dashboard setting for weekly pace working days, defaulting to all days.
- Expose the setting through the existing settings API and settings UI.
- Apply configured working days to the backend weekly credits pace schedule math.
- Keep the existing linear all-days behavior unchanged for existing installs.

## Impact

- Dashboard settings model, migration, repository/service/API schema, and audit changed-field tracking.
- Weekly credits pace backend calculation.
- Settings UI schema/control and frontend tests.
- Integration tests for settings round-trip, validation, audit, and dashboard pace projections.
