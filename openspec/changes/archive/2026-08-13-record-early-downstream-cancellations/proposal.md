## Why

The raw Responses stream already records a downstream disconnect after visible
output as `cancelled` with error code `client_disconnected`, but cancellation
before the first upstream event bypasses that settlement helper. The canonical
Responses API spec also still calls the existing `cancelled` outcome an
`error`, so it disagrees with the runtime and its regression test.

## What Changes

- Record every downstream `CancelledError` or `GeneratorExit` as `cancelled`
  with downstream `client_disconnected` metadata, including before visibility.
- Keep the upstream account healthy because the client ended the request.
- Correct the stale raw-stream contract to name the existing `cancelled`
  request-log status.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: downstream cancellations use one client-side
  settlement before and after the first visible stream event.

## Impact

- Code: `app/modules/proxy/_service/streaming/mixin.py`
- Tests: `tests/unit/test_proxy_utils.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`
