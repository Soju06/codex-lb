## Why

A direct Responses WebSocket can receive the client's next frame while the
reader for the previous upstream generation is finishing. Cancelling and
recreating the downstream receive during that handoff can consume a queued
frame without processing it, while processing the frame before a simultaneous
upstream completion can let newer work overtake an earlier replay. A failed
downstream receive is different: replaying after the client side is already
unrecoverable can reconnect, reserve capacity, and resend work that no client
can consume. A completed downstream disconnect is terminal for the same
reason and must stop the session before any replay work starts. These outcomes
are not limited to one `asyncio.wait` return: the upstream can finish while the
newer frame is being prepared, while continuity ownership is resolved, or
while response-create admission is pending. The retained downstream receive
can likewise become terminal while the retiring upstream is being closed or
its lease is being released, or while a replacement connection or capacity
lease is already in flight.

## What Changes

- Keep one owned downstream receive operation alive across polling, idle
  checks, and clean upstream-generation retirement.
- When both operations complete together, abort on a failed, cancelled, or
  disconnected downstream before reconnecting. Give the earlier upstream
  replay precedence only over a successful `websocket.receive`, preserving
  that received frame for later processing.
- Re-harvest upstream completion after request preparation and ownership
  checks and while admission is pending. Keep one prepared newer request under
  explicit ownership until the earlier replay finishes, then admit and send it
  exactly once.
- Recheck a retained downstream receive before each replay side effect. If a
  terminal outcome becomes observable while connection or capacity acquisition
  is already in flight, settle the newly acquired resources and stop before
  replay is sent.
- Detach and await the owned downstream receiver on every session exit. Log and
  contain a secondary receiver failure so it cannot mask the primary handoff
  failure or skip upstream, lease, admission, gate, and pending cleanup.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: direct Responses WebSockets preserve receive
  ownership and request ordering across clean upstream handoffs.

## Impact

The change is limited to direct `/backend-api/codex/responses` and
`/v1/responses` WebSocket lifecycle handling and its regression coverage. It
does not change account routing, affinity selection, replay eligibility, or
the downstream event contract.
