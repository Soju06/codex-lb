## Why

Policy invalidation during recovery-probe commitment or process-seed persistence can escape the final unbound selection fence. Cancellation during WebSocket rejection or late sticky release can strand provisional admission resources.

## What Changes

- Fence policy invalidation at recovery-probe commitment and after process-seed persistence.
- Hand rejected WebSocket frames to owned finalization before cancellable resource release.
- Reuse cancellation-deferring cleanup for late selection exits.
- Preserve existing regressions and add observable dispatch and settlement coverage for the failing schedules.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: specify the final selection observation boundary and cancellation ownership of pre-dispatch rejection.

## Impact

Proxy selection and WebSocket request finalization, regression tests, and account-routing contracts. No schema, dependency, configuration, dashboard, or migration revision changes. Published database histories and disabled-policy semantics remain supported.
