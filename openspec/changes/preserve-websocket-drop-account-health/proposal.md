## Why

Native Responses WebSocket receive closes can reach the relay without a typed
error code. They are still upstream failures: treating that unclassified case
as account-neutral leaves a closing or resetting account eligible for immediate
selection. Only a classified process-wide network failure is account-neutral.

## What Changes

- Preserve the existing account-health signal for unclassified upstream
  WebSocket receive closes.
- Keep `proxy_network_unavailable` account-neutral, with no replay of an
  ambiguously delivered request.
- Cover both the direct/routed unclassified receive path and a classified relay
  error at the product relay entry point.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: upstream WebSocket close health classification keeps
  the process-wide network exception narrow.

## Impact

- `app/modules/proxy/_service/websocket/mixin.py` relay failure settlement.
- `tests/unit/test_proxy_utils.py` direct WebSocket relay regressions.
- No database migration, API shape, or setting change.
