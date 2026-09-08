# Outbound HTTP Clients Context

## Purpose and Scope

This note records implementation decisions behind the outbound client layer that do not change the normative contracts in `spec.md`. It currently covers how the TLS verification context is shared across connectors.

## Shared TLS verification context

Every outbound aiohttp connector (the shared client generations built by `_build_http_client`, the per-call `create_codex_session()` sessions used by routed streams, bridge websockets, usage polls and token refreshes, the per-request SOCKS `_socks_proxy_connector`, and the dashboard proxy-endpoint probe) verifies upstream certificates with the same policy: `ssl.create_default_context()` plus the certifi bundle loaded on top.

Before the `perf-shared-ssl-context` change each of those call sites built a fresh `ssl.SSLContext`. Building one parses the system trust store and the certifi PEM (~120 CAs): measured at ~7.5 ms CPU and ~650-740 KB RSS per copy on x86 with Python 3.14, roughly 2-3x that on the production Neoverse-N1 host. On a `workers=1` deployment with several upstream calls per second that cost showed up as ~1.75-2.5% of event-loop-thread wall time in the non-GIL py-spy profile (OpenSSL releases the GIL while parsing, so `--gil` profiles do not see it) and as ~120 MB of duplicated X509 stores across the ~170 live sessions found in a heap probe.

`app/core/clients/http.py` now exposes `_shared_ssl_context()`, a `functools.cache`d accessor around the unchanged `_build_ssl_context()` constructor, and every connector listed above uses it. Rationale for treating this as a pure refactor rather than a spec delta:

- No wire change. The context carries the same verification mode, hostname checking, protocol floor, and trust store as a per-call build (asserted by `test_shared_ssl_context_matches_a_fresh_build_verification_policy`); client-side contexts here do not enable TLS session resumption across connectors, so each connection still performs the same handshake it did before.
- Nothing mutates the context after construction (`SSLContext` is treated as immutable-after-build throughout the codebase), which is the same sharing pattern aiohttp uses for its own module-level default contexts.
- The shared client generations already reused one context per generation; the change only extends that reuse to the per-call sessions.

The one operational consequence: updates to the certifi bundle or system CA store on disk are picked up only after a process restart. Per-call sessions previously re-read the bundle on every upstream call; the shared client had always behaved this way per generation, and there is no supported flow that swaps CA bundles under a running codex-lb, so no requirement changes. `close_http_client()` clears the cache during shutdown, and `_reset_shared_ssl_context()` exists for test isolation (tests that patch `_build_ssl_context` rely on the cache being empty when they start).

Deferred on purpose: sharing one routed `TCPConnector`/`ClientSession` across per-call Codex clients (connection reuse through the proxy) is a separate change with connection-lifetime semantics of its own; on Docker deployments the native egress helper already pools routed connections.

## Native Responses SSE ownership (2026-09-08)

The `http_sse_v1` capability moves byte framing for direct and account-routed streaming Responses
into the existing Rust egress library. Python supplies the configured idle
interval and event byte limit; Rust applies them while reading the body.
For example, an event split over several active body reads must not time out
just because Python has not yet received a complete event. Python retains
normalization, terminal detection, archives, selection, health, and replay.

Complete events cross IPC as UTF-8 text fragments of at most 16 KiB, with a
`more` flag. This bounds line/queue expansion for control characters and invalid
UTF-8 while avoiding Python byte scanning and base64 decoding. Shared fixtures
pin the legacy framing behavior, including CR/LF splits, whitespace, EOF
residue, and limits measured in original body bytes. The adapter only joins
text fragments. An incomplete IPC event at clean EOF fails the protocol.

HTTP error bodies and requests without SSE options retain raw body delivery.
Compact framing remains a separate future cutover. Missing helpers
keep the pre-dispatch Python fallback; installed helpers without the capability
fail before dispatch. No dispatched request is replayed through that fallback.
Response close finishes its owned cancellation handshake even inside an already
cancelled Starlette/AnyIO scope, then propagates cancellation. Other requests
sharing the helper continue normally.

Routed streaming uses typed `native_sse` options with unbuffered consumption
through `CodexClient`. Each endpoint attempt receives the same options; the
options never reach aiohttp keyword arguments. Keeping the native response
type avoids the raw-body wrapper hiding its framed-event interface. Endpoint
fallback and trace metadata remain Python-owned. The locally created client
finishes asynchronous session close before propagating cancellation; borrowed
clients retain their caller's lifecycle ownership.
