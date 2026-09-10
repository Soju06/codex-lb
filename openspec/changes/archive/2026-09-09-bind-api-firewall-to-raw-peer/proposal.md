## Why

The API firewall currently treats the downstream projected client as its socket source, so proxy-header projection can change whether a protected HTTP or WebSocket request is allowed. Firewall trust must instead start from the server-captured raw peer while preserving projection for downstream consumers.

## What Changes

- Resolve protected HTTP and WebSocket firewall identity from the captured raw socket peer.
- Continue applying the existing trusted forwarded-chain algorithm only when firewall proxy trust is enabled and the raw peer belongs to the configured trusted CIDRs.
- Fail closed when raw-peer capture is absent and the allowlist is non-empty; preserve empty-allowlist allow-all behavior.
- Preserve downstream projected client and scheme behavior unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-firewall`: Bind HTTP and protected WebSocket firewall enforcement to the captured pre-projection socket peer.

## Impact

Affected seams are the API firewall middleware, protected WebSocket firewall check, and their HTTP/WebSocket regression coverage. No setting, dependency, API schema, cache contract, forwarded-header parser, projection behavior, or non-firewall consumer changes.
