## Why

The `/internal/drain/*` control-plane endpoints carry no authentication. Their
only gate is a loopback check on `request.client.host`, which is Uvicorn's
forwarded projection rather than the transport peer. When `FORWARDED_ALLOW_IPS`
trusts the caller's peer, a remote client that sends `X-Forwarded-For:
127.0.0.1` passes that gate.

A remote caller can then start a drain, after which every non-allowlisted route
answers 503 and readiness fails. Adding the drain-deadline header routes the
same request to the one-way shutdown barrier, which `stop_drain()` refuses to
undo, so the replica stays down until it is restarted.

## What Changes

- Authorize the three `/internal/drain/*` endpoints against the
  launcher-preserved raw socket peer instead of the projected client.
- Fail closed with 403 when raw-peer capture is unavailable.
- Replace the three duplicated inline guards with one helper.

## Capabilities

### Modified Capabilities

- `graceful-shutdown`: internal drain control must authorize the raw socket
  peer, and must not accept a forwarded projection in its place.

## Impact

Limited to the health module's internal drain guard and its regression tests.
The loopback preStop contract, the drain deadline semantics, and the response
schemas are unchanged. No setting, migration, dependency, or dashboard-visible
behavior is added.
