## Why

The native direct HTTP cutover aligns HTTP/2 and the stable Codex TLS profile, but it launches one helper and creates one reqwest client for every request. That forces a new TLS connection per model refresh or Responses turn and remains distinguishable from Codex's pooled HTTP/2 behavior.

## What Changes

- Keep one native helper process alive per codex-lb worker and multiplex concurrent requests by opaque request identifiers.
- Reuse reqwest clients and their HTTP/2 connection pools for requests with the same proxy and connect-timeout policy.
- Cancel only the abandoned native request while keeping unrelated streams and the helper alive.
- Fail all in-flight requests if the helper exits, then restart it only for a later new request without replaying an ambiguous POST.
- Close and await the persistent helper during application shutdown.

## Capabilities

### Modified Capabilities

- `outbound-http-clients`: packaged native egress persists and reuses compatible HTTP/2 pools while retaining the existing replay-safe boundary.
- `responses-api-compat`: cancellation becomes per-request and does not tear down unrelated multiplexed streams.
- `deployment-installation`: the packaged helper protocol supports long-lived multiplexed operation without a sidecar or new operator setting.

## Impact

The Python native-egress adapter, Rust helper protocol, application shutdown, Docker artifact tests, and direct HTTP/SSE tests are affected. Routed and WebSocket egress remain out of scope.
