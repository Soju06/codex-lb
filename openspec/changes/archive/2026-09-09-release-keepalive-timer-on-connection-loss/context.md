# Context: release-keepalive-timer-on-connection-loss

## Incident (2026-09-03)

Single-worker deployment behind HAProxy, 2 GiB memcg. Memory climbed
monotonically over ~2 h until the container was OOM-killed at 07:03 UTC. A heap
probe showed ~18k retained `HttpToolsProtocol` / `RequestResponseCycle` /
`TCPTransport` graphs per two hours of uptime while uvicorn's
`server_state.connections` was empty. Raising nothing else, setting
`UVICORN_TIMEOUT_KEEP_ALIVE=300` stopped the climb — the retention was bounded
by the keep-alive window, not by traffic.

## Mechanism

1. `on_response_complete` arms
   `loop.call_later(timeout_keep_alive, self.timeout_keep_alive_handler)`.
   The handle holds a bound method, i.e. a strong reference to the protocol.
2. Both stdlib asyncio and uvloop keep every pending `TimerHandle` in the loop
   (uvloop: `loop._timers`) until it fires or is cancelled.
3. `connection_lost(exc)` in uvicorn's `httptools_impl` and `h11_impl` runs
   `self._unset_keepalive_if_required()` inside `if exc is None:`. A peer FIN
   arrives as `exc is None`; an RST, `ECONNRESET`, `ETIMEDOUT`, or any read
   error arrives with `exc` set and skips the cancel.
4. The transport drops its protocol reference, but the protocol still owns the
   transport wrapper, flow control, cycle, and scope (all request headers
   including `Authorization`, a copy of `app_state`). Retention chain:
   `loop timers -> TimerHandle -> bound timeout_keep_alive_handler -> protocol
   -> {transport, cycle, scope}`.

HAProxy tears down idle server-side connections with RST (verified 50/50 with
HAProxy 3.0), so behind it every front-door request produced one leaked graph.
Clean FIN closes are unaffected, which is why direct-connect testing never
showed it and why the leak scaled with proxy traffic, client aborts, and surges.

## Why cancel in a subclass override, after `super()`

`_unset_keepalive_if_required` touches only `timeout_keep_alive_task`; calling
it after the stock `connection_lost` leaves the parser reset, the
`h11.ConnectionClosed` send, `cycle.disconnected`, and flow-control resume
exactly as upstream has them. On a clean close the base already cancelled the
timer, so the extra call is a no-op. Cancelling an already-fired handle is a
no-op too. The error path deliberately does not call `transport.close()`: the
loop has already force-closed the transport and the stock semantics are kept.
Once the timer reference is gone the only remaining path to the protocol is a
still-running ASGI task, which `run_asgi` releases in its `finally`, and
`on_response_complete` returns early on a closing transport so no new timer can
be armed after the loss.

The override lives in the same subclasses that decline h2c upgrade offers.
Their canary tests were written so that an upstream fix for the upgrade defect
would signal "retire the subclass"; that note now says the subclass can only go
once the keep-alive canary (stock protocol still pins the protocol after an
error close) fails as well. Upstream status: uvicorn `master` still has the
same `connection_lost` body at the time of writing.

## Why the default returns to 300 s

`#698` introduced `--timeout-keep-alive` at 300 s, stating that uvicorn's 5 s
default could let Codex CLI write a compaction POST into a socket the server
was closing. `#729` raised it to 7200 s with the text "Codex CLI reuses local
connections for large compact POSTs"; neither PR recorded a measurement or a
rationale for its number (see "Provenance and audit" below). The race is prevented by `server idle window > client pool idle
timeout (+ margin)` and is independent of request size:

| Client | Pool idle timeout |
|--------|-------------------|
| reqwest/hyper default (Codex CLI's HTTP stack) | 90 s |
| This repo's Rust egress client | 120 s |
| Go `net/http` default | 90 s |
| aiohttp / httpx / undici defaults | 15 s / 5 s / 4 s |

300 s clears every known client by more than 2x; 7200 s buys nothing for the
race but holds every idle socket (and, before this change, every leaked
protocol) 24x longer. Production has run 300 s since the incident without
stale-socket reports. Operators with a direct-connect client whose pool idle
exceeds 300 s raise `UVICORN_TIMEOUT_KEEP_ALIVE` explicitly; the knob is an
internal tunable and stays out of `.env.example` (simplicity gate P2).

## Provenance and audit (2026-09-04)

The #698/#729 premise — "Codex CLI reuses local connections for large compact
POSTs" — is contradicted by codex-rs at `rust-v0.131.0` and `rust-v0.133.0`
(the tags bracketing those PRs) and at HEAD: `core/src/client.rs`
(`build_api_transport` -> `create_client_for_route`) builds a fresh
`reqwest::Client` via `login/src/auth/default_client.rs` for every `/responses`
and `/responses/compact` call, so Codex CLI holds no idle pooled connection to
codex-lb and FINs the socket as soon as the SSE body completes (probed twice
against a plain-HTTP server). reqwest 0.12.28's default `pool_idle_timeout` is
90 s and codex-rs never overrides pool settings (`http-client/src/client_builder.rs`
sets only TLS, headers, redirects, connect timeout, cookies). Codex also retries
transport errors itself: `request_max_retries` (default 4) replays the in-memory
body at the request layer (`codex-client/src/retry.rs`,
`codex-api/src/endpoint/session.rs`) and `stream_max_retries` (default 5) covers
the stream layer, so a keep-alive race costs one retry rather than a
user-visible failure. The safety invariant is therefore `server idle window >
every client's pool idle timeout + margin` (practically S >= 2C): uvicorn's
stock 5 s violates it, 300 s gives 3.3x over reqwest's 90 s, and 7200 s adds
nothing because the client side has closed every idle connection by 180 s.
uvicorn's timer never bounds an in-flight request and does not apply to
WebSocket connections. The retention leak fixed by this change is the
`connection_lost` `exc is None` gate described above; it is independent of the
default value, which only bounded how long each leaked graph survived.

## Verification evidence

- Fake-transport tests (stdlib loop): the weakref-after-`gc.collect()`
  assertion fails on the stock protocols and passes with the override; both
  loops behave alike because the reference is held by the handle object.
- Real-socket reproduction, 300 connections each completing one GET and then
  closing with `SO_LINGER(0)` (RST): before the change 300/300 protocols
  survive `gc.collect()` on asyncio 3.14 and uvloop 0.22 with the timer state
  `ARMED` and `connections` empty; after the change 0/300 on both loops. FIN
  close is 0/300 before and after.
- CPU impact is nil (memory-only; no profiler samples in
  `timeout_keep_alive_handler` / `connection_lost`).
