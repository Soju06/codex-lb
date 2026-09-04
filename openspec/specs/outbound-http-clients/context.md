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
