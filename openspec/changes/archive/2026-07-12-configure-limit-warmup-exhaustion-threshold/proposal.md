## Why

Limit warm-up currently requires the previous quota sample to report exactly
100 percent used before a reset-confirmed warm-up can run. Some upstream usage
payloads plateau at 99 percent while still representing a practically exhausted
window, so those accounts never create warm-up attempts after reset.

## What Changes

- Add a dashboard setting for the limit warm-up exhausted threshold, defaulting
  to 99 percent.
- Use that threshold when deciding whether the pre-reset sample was exhausted.
- Keep reset confirmation, per-account opt-in, cooldown, and post-reset
  availability checks unchanged.
- Expose and persist the setting through the settings API and dashboard UI.

## Impact

- Dashboard settings model, migration, repository/service/API schema, and audit
  changed-field tracking.
- Limit warm-up candidate selection.
- Settings UI schema/control and frontend tests.
- Backend unit and integration tests for settings and warm-up behavior.
