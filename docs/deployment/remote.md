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

---

*Specs: [deployment-networking](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-networking) · [api-firewall](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/api-firewall)*
