## Why

The planner accepts malformed timezone keys such as `/Europe/Stockholm`, then
raises `ValueError` while forecasting or evaluating a cold account. A saved
operator setting must not interrupt user traffic.

## What Changes

- Reject non-empty invalid timezone updates before persisting any settings.
- Preserve whitespace normalization and omitted, null, or blank update behavior.
- Extend the existing UTC fallback to malformed timezone keys already stored.
- Cover settings updates, forecast recovery, and cold-account routing.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `quota-phase-planner`: Validate timezone updates and tolerate legacy invalid keys.

## Impact

Quota planner settings API and timezone conversion. No new settings, database
migration, dependencies, routing policy, or dashboard layout changes.
