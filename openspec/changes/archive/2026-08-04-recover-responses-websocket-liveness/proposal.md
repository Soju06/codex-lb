## Why

An established upstream Responses WebSocket can remain silently black-holed after a host network transition such as VPN disconnection. Because Responses sockets currently disable the transports' existing ping/pong liveness checks, downstream keepalives can keep a conversation waiting until the much longer request timeout instead of terminating promptly so the client can reconnect.

## What Changes

- Enable the existing WebSocket transport liveness checks for direct and routed upstream Responses connections, using the current downstream WebSocket idle-timeout setting as the liveness budget.
- Classify transport-detected ping/pong timeouts with a stable internal error code.
- Treat a post-send liveness timeout as account-neutral and terminal for pending work, without transparent replay, because upstream request acceptance is ambiguous.
- Retire the affected upstream socket so a later client retry establishes a fresh network route.
- Add transport, direct WebSocket relay, and HTTP bridge regression coverage for the liveness and settlement invariants.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Require bounded upstream Responses WebSocket liveness detection and safe handling of liveness timeouts across direct WebSocket and HTTP bridge clients.

## Impact

- Affects the shared upstream WebSocket adapters and both Responses relay owners.
- Reuses existing aiohttp heartbeat, websockets ping timeout, and configuration; no new dependency, setting, API, schema, migration, or dashboard surface is introduced.
- A stalled conversation terminates after the configured liveness budget and relies on the downstream client to retry on a fresh connection.
