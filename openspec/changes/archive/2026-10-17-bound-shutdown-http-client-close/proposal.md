## Why

The shutdown path bounds the scheduler-lease release and metrics-server wait,
but awaits managed HTTP client closure without a deadline. A stalled transport
can therefore prevent process-death marking and database disposal even after
request drain and bridge-ring stale marking have completed.

## What Changes

- Limit the managed HTTP client close wait to 10 seconds.
- Retain the close task until it settles without cancelling client-owned
  transport cleanup on timeout.
- Consume close failures and self-cancellation so later teardown still runs.
- Test the full lifespan with a stalled HTTP close, alongside focused completion,
  failure, cancellation and task-ownership cases.

## Capabilities

### Modified Capabilities

- `graceful-shutdown`: bounded managed HTTP client close during final teardown.

## Impact

Only the managed HTTP close wait in application shutdown changes. Existing
request drain, bridge-ring stale marking, leader-lease release, native-egress
close and database clean-marker rules remain intact. No setting, schema,
dependency or deployment change is added.
