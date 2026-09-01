## Why

A quota warmup can finish its upstream probe and receive exact token usage, but
caller cancellation during API-key reservation finalization currently replaces
that measured usage with a failed zero-token settlement.

## What Changes

- Finish owned warmup reservation finalization before propagating cancellation
  once the probe has returned exact usage.
- Preserve zero-usage failure only when cancellation occurs before the probe
  returns measured usage. Cancellation during finalization preserves measured
  usage.
- Keep request logging and decision completion outside the deferred boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `quota-phase-planner`: preserve measured warmup usage during cancellation.

## Impact

- Shared quota warmup service and one integration regression.
- No setting, dependency, route, migration, reservation, or scheduler policy.
