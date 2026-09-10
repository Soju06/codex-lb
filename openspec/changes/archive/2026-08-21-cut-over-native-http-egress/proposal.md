## Why

The staged Rust helper negotiates HTTP/2 with the Codex upstream, but codex-lb still sends production Responses HTTP/SSE and model-discovery traffic through Python/OpenSSL. The server can therefore continue to distinguish the load-balancer path by ALPN, HTTP version, and TLS implementation even after header alignment.

## What Changes

- Package the pinned native egress helper in the official Linux container image and discover that packaged executable without adding an operator setting.
- Prefer the native helper for direct HTTP/SSE Responses requests and direct model discovery while retaining the existing Python clients when the helper is absent.
- Preserve replay safety: only helper unavailability before dispatch may fall back to Python for a non-idempotent Responses request; ambiguous or post-dispatch native failures remain terminal for that attempt.
- Preserve cancellation, SSE framing, error mapping, rate-limit ingestion, circuit-breaker accounting, and request observability across the native path.
- Keep routed upstream-proxy traffic and WebSocket traffic on their existing transports in this change.

## Capabilities

### Modified Capabilities

- `outbound-http-clients`: direct Codex HTTP calls prefer the packaged native transport with a replay-safe fallback boundary.
- `responses-api-compat`: direct Responses HTTP/SSE streams preserve their existing public contract over native egress.
- `deployment-installation`: the official Linux container includes the native helper while non-container installs remain zero-configuration and safely degrade when it is unavailable.

## Impact

Direct model discovery and direct Responses HTTP/SSE egress, the Docker build, native helper protocol, and focused transport tests are affected. No dashboard setting, environment variable, public request shape, database schema, or required sidecar is added. Routed proxy and WebSocket traffic remain distinguishable until later changes.
