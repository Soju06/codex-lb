# Proposal: disable-upstream-websocket-compression

## Why

The direct-egress upstream websocket transport (`websockets.asyncio.client.connect` in
`app/core/clients/proxy_websocket.py`) passes no `compression` kwarg, so the websockets
library default (`compression="deflate"`) silently offers and negotiates `permessage-deflate`
with the upstream endpoint. No commit, spec, or comment ever chose this: the two sibling
upstream transports already run uncompressed — the routed aiohttp path uses aiohttp's
default `compress=0` (off), and the raw-handshake transport in `app/core/clients/proxy.py`
sets `compress=False`/`compress=0` explicitly. Per-frame zlib decode of high-rate upstream
event streams shows up as a measurable CPU leaf (~2.6% of profiled CPU) on the
single-weak-core proxy host, where CPU — not LAN/WAN bandwidth — is the scarce resource.

## What Changes

- Pass `compression=None` at the single direct-egress `websocket_connect(...)` callsite,
  so upstream direct-egress websockets (Responses websocket and realtime live sideband,
  which share the callsite) no longer offer `permessage-deflate` in the handshake.
- The client-facing socket is untouched: the existing normative requirement that the
  server MUST continue to negotiate `permessage-deflate` on the client-facing websocket
  (responses-api-compat, downstream ingress budget requirement) is unchanged, and uvicorn's
  `ws_per_message_deflate` default stays enabled.
- The routed aiohttp path and raw-handshake transport are untouched (already uncompressed).

## Owner-visible caveats

- **Codex CLI fingerprint divergence**: codex-lb impersonates the Codex CLI persona on
  the upstream handshake, and the native Codex CLI (tungstenite with
  `DeflateConfig::default()`) DOES offer `permessage-deflate`. Dropping the
  `Sec-WebSocket-Extensions` offer makes the direct path's handshake differ from the
  native client. Mitigating evidence: the routed aiohttp path and the raw-handshake path
  already send no extension offer today and are accepted in production, so extension
  parity is not currently maintained anywhere.
- **Realtime live sideband shares the callsite**: the change applies to both
  `_RESPONSES_WEBSOCKET_POLICY` and `_LIVE_SIDEBAND_WEBSOCKET_POLICY` sockets. Live
  sideband frames (base64 audio) compress poorly anyway; if per-policy compression is
  ever wanted, the kwarg can be lifted into `_UpstreamWebSocketPolicy`.
- **WAN ingress bandwidth** from the upstream increases (JSON event streams compress
  ~4-8x); this is a cost trade only, not a correctness change.
- **Falsification test**: if a post-deploy profile still shows the `permessage_deflate`
  decode leaf, the cost was downstream uvicorn decode (spec-protected, must keep) and
  this change is a CPU no-op; revert is one line.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`: adds a requirement that direct-egress upstream websockets do
  not offer `permessage-deflate`. The client-facing `permessage-deflate` MUST is
  unchanged.

## Impact

- `app/core/clients/proxy_websocket.py`: one kwarg (`compression=None`) on the shared
  direct-egress `websocket_connect` call; persona headers, subprotocols, ping-timeout
  watchdog, max_size, and proxy resolution are unchanged.
- `tests/unit/test_proxy_websocket_client.py`: kwargs assertion pins
  `compression is None` on the direct transport contract.
