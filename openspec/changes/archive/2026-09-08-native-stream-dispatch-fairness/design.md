# Fair native event dispatch

## Context

Buffered helper stdout can satisfy consecutive reads without suspending. The
reader can then fill a healthy consumer's bounded queue in one scheduling turn.
Current main yields for headers and SSE events, but not raw HTTP chunks or
WebSocket messages.

## Decisions

Yield after each accepted event. Keep nonblocking enqueue, the 64-event bound,
generation checks, and per-request overflow cancellation. Awaiting a full queue
would allow a stalled request to block every other request on the helper.

Verify direct and routed Responses consumption with deterministic buffered IPC
bursts, covering framed SSE, raw JSON success, and raw HTTP errors. Retain the
stalled-consumer isolation regression. No wire or routing policy changes.
