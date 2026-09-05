# Remote Access

Running codex-lb on a server and connecting from other machines involves three pieces: the one-time dashboard bootstrap token, API keys for clients, and (usually) a reverse proxy.

## First login

Setting the initial dashboard password remotely requires a one-time bootstrap token printed to the server logs — see [Getting Started](../getting-started.md#remote-setup-bootstrap-token).

## Client access

Remote clients hit the protected proxy routes, which reject non-local requests until proxy authentication is configured. Enable [API key auth](../api-keys.md) and give each client a key from the dashboard.

## Reverse proxy

When codex-lb sits behind a reverse proxy (nginx, Traefik, Caddy, Authelia, ...):

- **Forward WebSocket upgrades.** Codex streaming uses WebSockets on `/backend-api/codex/responses`; a proxy that only forwards plain HTTP silently degrades to POST fallback. See [verify WebSocket transport](../client-setup.md#verify-websocket-transport).
- **Declare the proxy as trusted** so codex-lb sees real client IPs from `X-Forwarded-For`:

```bash
CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS=true
CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS=172.18.0.0/16
```

Only sources inside the trusted CIDRs may set forwarded headers; everything else is treated as the direct peer address.

- **Optionally delegate dashboard auth** to the proxy with `trusted_header` mode — see [Authentication](../authentication.md).
- **Leave the idle keep-alive window alone unless a client needs more.** codex-lb closes idle client connections after 300 s (`--timeout-keep-alive` / `UVICORN_TIMEOUT_KEEP_ALIVE`, process environment only). The value must exceed the largest connection-pool idle timeout of your proxy and clients by a safety margin that absorbs the network round-trip and timer scheduling (practically `S >= 2C`; reqwest default: 90 s, so 300 s leaves 3.3x; Codex CLI itself opens a fresh connection per `/responses` request) so a pooled connection is never reused as the server closes it; raising it into hours only holds idle sockets longer. If a reverse proxy fronts codex-lb, keep the proxy's *upstream* idle/pool timeout below `--timeout-keep-alive`, or raise `UVICORN_TIMEOUT_KEEP_ALIVE` above it. The race window is one RTT wide at the server's timeout and does not depend on request body size, so large compaction POSTs need no extra allowance.

---

*Specs: [deployment-networking](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-networking) · [api-firewall](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/api-firewall) · [http-ingress-limits](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/http-ingress-limits)*
