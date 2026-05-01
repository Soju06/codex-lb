## Why

When a local `codex-lb` instance is alive but cannot complete a proxy request before any downstream-visible output starts, clients currently receive a local failure even if another trusted `codex-lb` peer could serve the same request. Operators need an optional peer fallback path that improves availability for pre-output failures without changing process-down failover semantics or risking duplicate streamed output.

## What Changes

- Add an opt-in peer fallback path for proxy requests that fail locally before response headers, SSE events, body bytes, or websocket messages are sent downstream.
- Route eligible HTTP and SSE proxy requests to configured peer `codex-lb` instances when the local instance cannot complete local account selection, token refresh, upstream connection, or upstream first-byte work within the request budget.
- Prevent fallback loops by marking forwarded peer attempts and rejecting or not re-forwarding requests that already came from a peer.
- Keep fallback explicitly distinct from process-down failover: peer fallback applies only while the local service is running and handling the request.
- Defer websocket peer fallback unless an implementation can prove it preserves handshake, message ordering, timeout, and duplicate-output guarantees.

## Impact

- Code: proxy request orchestration, timeout handling, upstream error handling, and request tracing/logging
- Configuration: optional peer list and per-peer fallback controls
- Tests: HTTP and SSE fallback eligibility, loop prevention, no fallback after downstream-visible output, websocket deferral behavior
- Specs: `openspec/specs/proxy-runtime-observability/spec.md`
