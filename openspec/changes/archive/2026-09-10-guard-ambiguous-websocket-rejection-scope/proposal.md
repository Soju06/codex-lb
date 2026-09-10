## Why

A shared WebSocket failure can cover requests with different models or service tiers. Attributing it to the first pending request lets a completed probe for one scope clear a hold that might belong to another.

## What Changes

- Retain unknown scope for mixed-scope shared failures.
- Preserve known scope for request-specific errors and batches with one common scope.
- Prove through the operator probe route that ambiguous holds remain protected.

## Capabilities

### New Capabilities

### Modified Capabilities

- `account-routing`: distinguish attributable and ambiguous WebSocket rejection scope.

## Impact

The shared finalizer's scope assignment, routing requirements and regression tests. Penalty selection, reservation settlement, terminal logging and generation checks are unchanged. Refs #2327.
