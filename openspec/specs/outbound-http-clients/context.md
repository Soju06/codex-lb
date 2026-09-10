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
terminal detection, archives, selection, health, and replay policy.

Complete events cross IPC as UTF-8 text fragments of at most 16 KiB, with a
`more` flag. This bounds line/queue expansion for control characters and invalid
UTF-8 while avoiding Python byte scanning and base64 decoding. Shared fixtures
pin the legacy framing behavior, including CR/LF splits, whitespace, EOF
residue, and limits measured in original body bytes. The adapter only joins
text fragments. An incomplete IPC event at clean EOF fails the protocol.

HTTP error bodies and requests without SSE options retain raw body delivery.
Compact requests use separate content-aware framing and collection capabilities. Missing helpers
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

## Native HTTP stream interpretation

`http_responses_events_v1` adds `interpret_responses` to framing options and a
`responses_event` IPC result. The final text fragment includes `event_type` and
`python_normalization`; intermediate fragments have no type and a false marker.
Both text and type metadata are bounded at 16 KiB of UTF-8 and count toward the
queue byte budget. A longer type uses a Python handoff with no type metadata.
Missing or malformed metadata, mixed compact/stream options, and truncated
fragments fail before an event is trusted.

For example, a `response.text.delta` payload with ordinary text becomes
`response.output_text.delta` in Rust; Python can use the attached classification
without scanning SSE or parsing that payload. Error conversion needs request
context and stays in Python. An alias payload containing a float, oversized
integer, or escaped surrogate uses Python serialization to preserve its exact
legacy representation. Neither handoff starts a second HTTP request. Unchanged
mixed-line-ending events preserve their text; JSON arrays never become objects.
The Python transport remains the missing-helper implementation.

## Native Responses WebSocket ownership

`websocket_responses_events_v1` classifies Responses WebSocket JSON objects in
Rust and embeds their JSON payload in IPC. The Python WebSocket relay and HTTP
bridge reuse that decoded object for request matching, sequence tracking,
tool-call handling and lifecycle validation. Original text, numeric tokens,
duplicate-key precedence and WebSocket aliases stay unchanged. A string `type`
wins; otherwise an object `error` classifies as `error`. Public errors and
HTTP-specific normalization retain their Python policy owners.

For example, an integer larger than 64 bits crosses IPC without Rust numeric
conversion and remains a Python integer. Whitespace outside strings is removed
only in the embedded IPC object to preserve JSON-line framing; original frame
text is untouched. Invalid/non-object/unsupported JSON and frames over 1 MiB
remain opaque. Live calls do not opt in. The HTTP bridge preserves its legacy
SSE-field parsing for multiline or whitespace-prefixed frames.

The Python fallback is still supported, so its parser is active code. Retired
native-path branches must be removed in the migration that replaces them. Before
removal, audit intervening Python commits and extend shared Rust/Python fixtures
for applicable fixes. The ownership table and audit through `d3f63331d` are in
[the archived change](../../changes/archive/2026-09-08-native-websocket-event-interpretation/context.md).
That change includes a tracked benchmark script/result; the final synthetic
measurement shows no speedup (640 ms raw versus 674 ms interpreted).

## Native SSE output writes

The helper coalesces already framed SSE records into writes of at most 32 records
and 64 KiB of encoded JSON lines, flushing each existing 16 KiB body-read slice
before processing more input. A single protocol record that expands beyond the
byte budget through JSON escaping is emitted alone. There is no batching timer:
one ready event reaches the consumer even if upstream waits indefinitely for the
consumer's next action. Valid records also precede a framing failure from the
same read.

Accepted output bytes and their write offset live in the shared writer. If a
producer is cancelled during a partial write or buffered flush, the next writer
finishes those bytes before emitting its own record. For example, cancellation
of a large SSE event cannot splice a sibling request's JSON line into that event.
Compact, raw HTTP and WebSocket messages keep immediate writes through this same
cancellation-safe owner. Python queue fairness, bounds and replay policy are
unchanged. Benchmark methodology and limitations are recorded in the archived
`batch-ready-native-sse-output` change.

## Direct usage GET transport

Direct usage queries without an injected Python client prefer the existing
native helper after the direct-egress admission check. Python owns routes,
retry policy and UsagePayload validation; Rust owns each HTTP attempt and body.
Only a missing/unstartable helper on the initial request permits Python fallback.

For example, a 503 response whose body never ends closes before the next attempt
and uses the existing ExponentialRetry delay (1, 2, then 2 seconds). A truncated
200 body remains a transport error instead of becoming an invalid-payload 502.
Cancellation retires one exchange without closing the shared helper.

The adapter explicitly forwards aiohttp's default Accept-Encoding value because
the helper enables decompression only for requests that negotiate it. Charset,
empty-body and JSON-syntax handling follow the default Python session. Usage
credit consumption remains a separate call, and resolved routes retain
CodexClient ownership. Loopback probes validate these semantics; they do not
measure production performance.
