## Why

Issue #2033 reports a second terminal Responses frame or abrupt stream close when an account-health write fails after the terminal frame. Current main still reproduces the keyed cases.

## What Changes

- Catch and log ordinary post-terminal account-health write failures without changing the delivered response.
- Preserve reservation settlement before health writes and cancellation propagation.
- Cover first-event, later-event, and raised upstream errors through the Responses route.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: keep a terminal stream complete when its subsequent account-health write fails.

## Impact

Backend streaming retry finalization and Responses route regression tests. No settings, schema, frontend, or unmerged dependency.
