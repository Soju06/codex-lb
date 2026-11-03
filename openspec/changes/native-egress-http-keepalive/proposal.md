# native-egress-http-keepalive

## Why

When `upstream_stream_transport` is set to `"http"` (or when the proxy falls back to HTTP), long reasoning streams such as `gpt-6.1-sol` with `reasoning_effort: high` can pause for minutes before emitting the first token. During this period of thinking silence, no HTTP data chunks are emitted by the upstream model backend.

In `crates/codex-lb-egress/src/http.rs`, `ClientPool::get` constructs `reqwest::Client` without HTTP/2 PING keepalive frames:
```rust
let mut builder = reqwest::Client::builder()
    .use_rustls_tls()
    .http2_initial_stream_window_size(CODEX_H2_INITIAL_STREAM_WINDOW_SIZE)
    .http2_initial_connection_window_size(CODEX_H2_INITIAL_CONNECTION_WINDOW_SIZE)
    .http2_max_frame_size(CODEX_H2_MAX_FRAME_SIZE)
    .http2_max_header_list_size(CODEX_H2_MAX_HEADER_LIST_SIZE)
    .pool_idle_timeout(Duration::from_secs(120))
    .pool_max_idle_per_host(8);
```

While reqwest defaults TCP keepalive to 15 seconds, HTTP/2 keep-alive PING frames default to disabled (`None`). When an intermediate reverse proxy, CDN edge (such as Cloudflare on `chatgpt.com`), or stateful NAT firewall sits on the network path, an HTTP/2 connection with an active stream but no active frames during a long thinking pause can be severed by the peer due to transport-layer inactivity. When the peer resets the transport, `reqwest` reports `reqwest::Error.is_body() = true` and codex-lb logs `"native upstream response body failed"`, breaking in-flight agent turns.

TCP keepalive and HTTP/2 PINGs operate hop-by-hop to the immediate peer and cannot override upstream application-level backend timeouts. However, configuring active peer keepalives helps mitigate transport-layer inactivity drops on intermediate stateful firewalls and edge proxies during model thinking pauses.

## What Changes

1. **Explicit TCP Keepalive Idle Duration:**
   Configure `.tcp_keepalive(Duration::from_secs(15))` on `reqwest::Client::builder()` in `crates/codex-lb-egress/src/http.rs`, making the 15-second idle duration before TCP keepalive probes explicit in client pool construction.
2. **Configure HTTP/2 Keepalive Pings During Active Streams:**
   Enable `.http2_keep_alive_interval(Duration::from_secs(20))`, `.http2_keep_alive_timeout(Duration::from_secs(20))`, and `.http2_keep_alive_while_idle(false)` on `reqwest::Client::builder()` in `crates/codex-lb-egress/src/http.rs`. The 20-second interval emits PING frames during active in-flight streams to keep peer sessions active without modifying idle-connection pool behavior.
3. **Unit and Integration Tests:**
   - Add unit tests verifying that keepalive constants match the specification and that `ClientPool::get` builds clients with keepalive configuration using an idempotent crypto provider initializer.
   - Add integration test `stream_remains_active_across_quiet_pause_and_connection_reuses` in `codex-lb-egress-worker` verifying that a pooled keep-alive connection remains active across quiet stream periods and successfully serves multiple sequential requests on the same connection.
4. **Specification Delta:**
   Update `outbound-http-clients` spec to normatively require TCP keepalive and HTTP/2 active-stream keepalive on the native egress HTTP client builder.
