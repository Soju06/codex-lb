## Why

The HTTP Responses bridge removes a request from the pending deque as soon as
it matches an upstream `response.completed` event, but it may perform
asynchronous continuity work before delivering that event downstream. During
that work, request detachment can clear the mutable queue field. The completed
event and end-of-stream marker are then dropped even though completed-event
processing already claimed the request.

## What Changes

- Capture the downstream queue when completed-event processing removes the
  request from pending ownership.
- Use that captured queue for completed delivery after asynchronous bookkeeping.
- Keep emitting liveness frames, without manufacturing an idle timeout, while
  that completed-delivery operation is actively doing bookkeeping.
- Add stream-level regressions for slow and failed completed bookkeeping.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: completed HTTP bridge delivery survives request
  detachment after completed-event processing has claimed the pending request.

## Impact

- Code: completed-event delivery scope, stream liveness, and atomic detach.
- Tests: focused HTTP bridge cancellation/drain coverage.
- Failure and replay policy is unchanged.
- Configuration, database schema, and public response shapes are unchanged.
