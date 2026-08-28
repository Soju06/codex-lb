## Why

Trusted-proxy locality currently applies header-family precedence, so a client-preseeded loopback family can hide the remote identity supplied by a proxy. The same decision is used by dashboard bootstrap and protected proxy routes when API-key auth is disabled. Uvicorn can also replace the socket peer before that decision, making a forwarded identity look like the transport source.

## What Changes

- Preserve the raw HTTP/WebSocket socket peer before applying Uvicorn-compatible proxy projection exactly once on every project-owned launch path.
- For locality only, resolve each populated allowed identity family independently; accept unanimous results and fail closed on disagreement or malformed/unresolvable evidence.
- Use the preserved raw peer for trusted-proxy source checks and `proxy_unauthenticated_client_cidrs` while leaving generic resolver, firewall, logging, and bridge behavior unchanged.

## Capabilities

### Modified Capabilities

- `admin-auth`: Require raw-peer-backed identity-family consensus for trusted-proxy locality.
- `api-keys`: Prevent projected or conflicting identities from bypassing disabled API-key auth.
- `deployment-installation`: Preserve the raw peer before proxy projection on owned launch paths.

## Impact

The change is limited to request locality, the disabled-auth socket allowlist, the application proxy-header boundary, owned launch commands, their tests, and matching OpenSpec/context. It adds no setting, dependency, API, schema, migration, dashboard surface, or generic client-IP policy.
