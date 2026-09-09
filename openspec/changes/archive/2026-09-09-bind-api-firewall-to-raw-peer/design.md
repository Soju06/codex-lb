## Context

The outermost trusted-proxy middleware already captures the server-observed HTTP/WebSocket peer before Uvicorn-compatible projection mutates `scope["client"]`. Firewall enforcement still reads the projected client, however, and then passes it to the forwarded-chain resolver as if it were the socket peer. A forwarded identity can therefore incorrectly establish its own trust or directly satisfy the allowlist.

## Goals / Non-Goals

**Goals:**

- Make captured raw peer identity the sole socket-source input for HTTP and protected WebSocket firewall resolution.
- Preserve the existing trust-off, trusted-chain, malformed-chain, repeated-field, singleton-header, cache, and empty-allowlist semantics.
- Fail closed under an active allowlist when capture is absent.
- Keep projected client and scheme visible to all downstream consumers.

**Non-Goals:**

- Changing forwarded chain names or parsing, trusted CIDR semantics, caches, `FirewallService`, projection, scheme, locality, drain, dashboard/header auth, logs, `actor_ip`, bridge metadata, `FORWARDED_ALLOW_IPS`, or unauthenticated proxy CIDRs.
- Adding fallback identity, settings, dependencies, or migration behavior.

## Decisions

1. Both firewall entry points obtain their resolver socket input through `raw_socket_peer_host` at enforcement time. This reuses the capture contract and naturally returns no identity when capture is absent. Reading projected `client` as a fallback was rejected because it recreates the trust confusion.
2. HTTP retains `resolve_connection_client_ip` and its cache path; only the socket input changes. The direct `_resolve_client_ip` helper follows the same raw-peer contract so helper and middleware behavior cannot diverge.
3. WebSocket retains its existing repository/service flow; only the socket input changes. This keeps HTTP and WebSocket policy aligned without introducing a new abstraction.
4. Tests drive the real HTTP middleware and real WebSocket firewall helper through capture-then-project middleware. The adversarial matrix gives raw and projected peers opposite allowlist/trust status, so each result proves which identity controlled enforcement.

## Risks / Trade-offs

- [Direct tests or non-owned ASGI launchers omit capture] → Active allowlists deny rather than trusting a mutable projected identity; empty allowlists remain allow-all.
- [Firewall proxy trust accidentally changes] → Preserve the resolver, configured CIDRs, and accepted chain-header set unchanged, with existing positive and malformed-chain regressions retained.
- [Downstream behavior regresses] → Assert projected client and scheme after firewall enforcement in the real-surface ASGI proof.
