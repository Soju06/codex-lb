## Context

The live comparison used the same host and upstream target for direct Codex and codex-lb. Direct Codex offered HTTP/2 and a rustls/`aws-lc-rs` ClientHello, while codex-lb offered HTTP/1.1 through Python/OpenSSL. At HTTP level, model discovery exposed `Python/... aiohttp/...`, `Accept: application/json`, and no Codex originator. HTTP Responses requests configured as `always_http` still entered the enabled WebSocket session bridge because that bridge ran outside `_stream_with_retry`, where policy was otherwise enforced.

The installed Codex executable contains `reqwest 0.12`, `hyper 1`, `rustls 0.23`, `tokio-rustls 0.26`, `hyper-rustls 0.27`, and `aws-lc-rs`. Reproducing only its headers cannot reproduce its ALPN, cipher suite, extension ordering, or HTTP/2 behavior.

## Goals / Non-Goals

**Goals:**

- Remove deterministic Python identity leakage from model discovery.
- Make `always_http`, `smart`, `always_websocket`, explicit transport settings, and per-key overrides agree across bridge and non-bridge paths.
- Keep HTTP, SSE, WebSocket, TLS, and identity dimensions separately testable.
- Establish an internal native egress interface whose implementation can use the same Rust network stack as Codex.

**Non-Goals:**

- Claim that codex-lb is globally indistinguishable from Codex; source IP/ASN and intermediary behavior remain observable.
- Spoof a browser TLS profile or hard-code a captured JA3 value.
- Add a required sidecar, environment variable, or dashboard control.
- Replace every upstream call with the native path in one unsafe cutover.

## Decisions

- Model discovery uses a canonical Codex control-request header builder: bearer/account authentication plus `User-Agent`, `originator`, `version`, and `Accept: */*`. Route-specific clients receive the same header mapping.
- Bridge admission reuses the shared effective-policy and configured-transport resolvers. Explicit `http` disables the bridge, explicit `websocket` enables it, and otherwise the effective policy decides. When disabled, the existing `_stream_with_retry` path remains responsible for model/image/size resolution and retries.
- Parity reports never collapse the result into a single opaque fingerprint. They retain HTTP version/ALPN, TLS cipher/extensions, WebSocket negotiation, and normalized identity headers as separate evidence.
- The native boundary is request/response streaming rather than a JA3 knob. Its target implementation is a small Rust component pinned to the Codex release family's `reqwest`/`rustls`/`aws-lc-rs` stack. Python retains account selection, policy, retry, and observability ownership.
- The staged boundary lives in `app/core/clients/native_egress.py` and uses a one-request framed stream with `native/codex-egress`. The executable path must be injected explicitly; normal startup does not discover, build, or require the helper. The pinned implementation has been verified to negotiate HTTP/2, but production Responses traffic is not cut over until packaging, fallback, cancellation, and direct-vs-LB TLS captures pass as a separate change.

## Risks / Trade-offs

- [A first-party identity header can drift with Codex releases] -> Source the version from the existing Codex version cache and cover the complete mapping in tests.
- [Duplicated policy evaluation can diverge] -> Import the same resolver functions used by `_stream_with_retry`; do not reimplement string comparisons in the bridge.
- [Disabling the bridge loses socket continuity under `always_http`] -> This is the explicit contract of that policy; smart and always-WebSocket modes retain bridge reuse.
- [A native helper can complicate packaging] -> Keep the boundary internal and staged; do not make base startup depend on an unavailable binary until packaging and fallback tests exist.
- [Matching the Rust stack still does not hide network origin] -> Report egress-origin controls separately and never label stack parity as absolute indistinguishability.
