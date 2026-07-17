## Why

Owned Uvicorn launch paths currently allow server-level proxy-header handling to rewrite the ASGI client before codex-lb can distinguish the transport peer from a forwarded identity. As a result, a forwarded address can incorrectly satisfy the unauthenticated proxy-client allowlist even though the existing API-key contract requires the raw socket peer to be authoritative.

## What Changes

- Preserve the raw HTTP or WebSocket socket peer before any trusted proxy-header projection occurs.
- Move the owned launch paths' proxy-header projection into the application so raw-peer capture runs first while retaining Uvicorn's existing projected client and scheme behavior.
- Make `proxy_unauthenticated_client_cidrs` evaluate only the preserved raw peer for both HTTP and WebSocket authentication.
- Keep `FORWARDED_ALLOW_IPS` as the single source of truth for Uvicorn-compatible proxy trust, including its unset, empty, wildcard, and explicit-host semantics.
- Update direct Uvicorn launch examples and add regression coverage for middleware ordering, launchers, HTTP, and WebSocket behavior.
- Do not change proxy-identity conflict policy, locality resolution, API firewall behavior, request logging, bridge/drain/audit behavior, or dashboard throttling.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `deployment-installation`: Require owned server launch paths to preserve the raw transport peer before applying the existing trusted proxy-header projection.

## Impact

The change affects application middleware registration, conformance with the existing `api-keys` raw-peer allowlist contract, owned Uvicorn launch commands, direct-launch documentation, and focused tests. It introduces no new setting, dependency, API schema, database migration, dashboard surface, or general proxy-resolution policy.
