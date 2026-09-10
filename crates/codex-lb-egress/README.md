# Codex native egress library

This crate owns the reusable Codex-family HTTP, TLS, and WebSocket transport.
The separately packaged `codex-lb-egress-worker` crate owns the stdio process,
and `codex-lb-protocol` owns its versioned wire types. Keeping the binary shell
separate lets a future Rust application call this library without inheriting a
subprocess boundary.

See the [Rust migration architecture](../../docs/rust-architecture.md) for
workspace boundaries and migration rules.

The official Linux containers build and install it as
`codex-lb-native-egress`. Other installations do not require Rust or this
executable: model discovery, HTTP/SSE Responses requests, and Responses or Live
WebSockets fall back to the Python transport only when the helper is unavailable
before dispatch. This applies to both direct and account-routed egress.

For account-routed calls, Python remains the control plane: it selects the
account, resolves the ordered proxy endpoints, applies fallback safety, and
records route and health metadata. Each native command receives exactly one
concrete HTTP, HTTPS, SOCKS5, or SOCKS5H proxy endpoint and performs only that
attempt. The helper never chooses another endpoint, and an ambiguous native
POST, handshake, or frame is never replayed through Python.

One helper process lives per codex-lb worker. Newline-delimited HTTP and
WebSocket commands carry opaque request ids, and every response event echoes
that id. Before accepting commands, each generation negotiates its protocol
version and required capabilities with Python. WebSocket send/close commands
also carry a command id; the helper
acknowledges it only after the native sink accepts the frame. Concurrent HTTP
tasks share a reqwest pool partitioned by effective proxy URL and connect
timeout, while each WebSocket has a bounded command channel and an owning
stream task. EOF cancels all active tasks and exits cleanly.

The WebSocket stack pins the OpenAI Codex 0.150.1 fork revisions of
`tokio-tungstenite` and `tungstenite`, enables their default
`permessage-deflate`, handles ping/pong below the application relay, and runs a
finite pong watchdog. A helper or connection failure after dispatch is
terminal; the adapter never replays an ambiguous WebSocket frame through the
Python fallback.

The rustls `prefer-post-quantum` feature is intentional. It gives the helper
the same `X25519MLKEM768`-first group ordering and hybrid-plus-classical key
shares observed from the pinned Codex release family.
