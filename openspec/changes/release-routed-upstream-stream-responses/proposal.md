## Why

Routed upstream streams (dashboard proxy pools, SOCKS routes, the aiohttp fallback
behind the native egress helper) request the response unbuffered and consume it
through the SSE framer. The consumer almost always stops before body EOF: the
terminal `response.completed` event arrives while upstream keeps the keep-alive
tunnel open, the idle timeout fires, the request is cancelled, or the downstream
client disconnects. The only teardown on that path was closing the per-stream
`ClientSession`, which closes the socket but never releases the `ClientResponse`.
aiohttp then finalizes the still-acquired `Connection` from the cyclic GC and logs
`ERROR asyncio Unclosed connection` with the `ConnectionKey` repr, which carries the
proxy URL including its password.

Production (v1.25.0-beta.1, 2026-09-03) showed 44 to 1350 of these lines per hour
scaling with traffic, and a heap probe with ~170 dead
`ClientSession`/`TCPConnector`/`ClientResponse`/`SSLProtocol` graphs pinned until the
next full collection. The direct (non-routed) path is immune because it uses
`async with session.post(...)`, whose exit releases the response.

## What Changes

- The routed HTTP streaming path releases the raw upstream response before closing
  the per-stream client, on every exit: terminal-event break, idle timeout,
  cancellation, downstream `aclose()`, oversized event, and non-2xx error mapping.
- Release is duck-typed over the three response shapes the routed path can receive:
  aiohttp `release()`, native egress `aclose()`, and buffered or test responses that
  expose neither (no-op).
- SOCKS-routed responses (`_SessionOwnedResponse`) release the wrapped response before
  closing their private session, so the same guarantee holds there.
- The SSE consumer chain (`stream_responses` down to the transport attempt) closes
  its nested async generators under `contextlib.aclosing`, so a consumer's `aclose()`
  reaches the upstream teardown synchronously instead of via the asyncgen finalizer.
- Forwarded bytes, error mapping, retry classification, and cancellation semantics
  are unchanged; release runs strictly after the last yielded event block.

## Impact

- Affected specs: `outbound-http-clients`
- Affected code: `app/core/clients/proxy.py`, `app/core/clients/codex.py`
- Operator-visible: the `Unclosed connection` error lines for routed HTTP streams
  disappear, and the per-stream aiohttp graph is freed by refcount at stream end
  instead of by the cyclic GC.
- No new settings, no migration, no dashboard surface.
- On Docker installs of current `main` the native egress helper serves the routed hot
  path and aiohttp is fallback-only; this change covers that fallback, non-Docker
  installs, and SOCKS routes.
