# Raw socket peer preservation context

## Purpose and scope

This change closes the transport boundary beneath the existing [`api-keys` requirements](../../specs/api-keys/spec.md): an address projected from trusted proxy headers must never be mistaken for the socket peer when evaluating `proxy_unauthenticated_client_cidrs`. The matching launcher contract is defined in [`deployment-installation`](specs/deployment-installation/spec.md).

It intentionally does not decide which forwarded identity is authoritative, how conflicting header families are handled, or how locality, firewall, logging, bridge, drain, audit, and dashboard policies use projected identities.

## Decision and constraints

codex-lb captures the incoming ASGI client once, then delegates projection to Uvicorn's own middleware. This preserves a narrow raw identity for the unauthenticated allowlist while leaving `request.client` and the request/WebSocket scheme compatible for every existing consumer.

`FORWARDED_ALLOW_IPS` remains the only trust input because introducing another setting would split one transport policy across two sources of truth. Owned server paths must turn off the outer Uvicorn projection; otherwise the application receives an already rewritten client and no middleware can reconstruct the transport peer.

## Failure modes

- An external Uvicorn or FastAPI command that omits `--no-proxy-headers` captures a projected value instead of the raw peer. Shipped direct commands document and test the required flag.
- Missing raw-peer scope state fails closed for `proxy_unauthenticated_client_cidrs`; it never falls back to `request.client`.
- Middleware reordering could capture too late. Registration and end-to-end middleware tests lock the outermost ordering.
- Future Uvicorn parsing changes flow through the reused middleware rather than a codex-lb parser fork.

## Concrete example

Suppose the TCP peer is `10.0.0.8`, `proxy_unauthenticated_client_cidrs` contains only `192.168.65.1/32`, and a trusted proxy request supplies `X-Forwarded-For: 192.168.65.1` plus `X-Forwarded-Proto: https`. Downstream handlers still observe client `192.168.65.1` and scheme `https`, but the unauthenticated allowlist compares `10.0.0.8` and rejects the request. The same separation applies to a WebSocket upgrade, with `wss` projection preserved.
