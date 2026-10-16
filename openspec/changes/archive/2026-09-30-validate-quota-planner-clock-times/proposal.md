## Why

Planner settings accept impossible clock times such as `99:99`. Scheduling
then uses fallback hours, so the saved setting does not describe its behavior.

## What Changes

- Reject working-hour updates outside `00:00` through `23:59`.
- Preserve partial updates and reads of legacy settings.
- Cover rejected writes and valid boundaries through the dashboard API.

## Impact

- Affected spec: `quota-phase-planner`.
- Affected code: quota planner update request validation.
