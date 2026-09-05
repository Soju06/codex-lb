## Why

Direct Python WSS opens repeatedly construct a default SSL context even when the application aiohttp context is cached. Controlled separate-connection probes confirmed one default-context build per handshake; retained turns avoid that cost.

## What Changes

- Reuse a system-trust SSL context for the Python `websockets` WSS branch.
- Warm and reset it through the existing outbound-client lifecycle, preserving `ws://`, verification, proxy selection and native/routed ownership.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: Direct Python WSS context reuse without importing the aiohttp certifi trust policy.

## Impact

`app/core/clients/http.py`, `proxy_websocket.py`, existing client lifecycle and handshake tests. No new configuration, dependency, pool or public API.

Partial investigation follow-up for issue #2029; this scope does not independently close the broad performance issue.
