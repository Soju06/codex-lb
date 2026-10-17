## Context

The helper currently reads one request, creates one reqwest client, streams one response, and exits. A long SSE response means a merely sequential persistent protocol would block model discovery and other turns, so persistence and multiplexing must be introduced together.

## Goals / Non-Goals

**Goals:**

- Reuse native HTTP/2 connections across compatible direct requests.
- Support concurrent model and Responses streams without cross-delivery.
- Bound cancellation and failure to the correct request.
- Preserve unavailable-only Python fallback and no ambiguous POST replay.
- Shut down without orphan helper processes or reader tasks.

**Non-Goals:**

- Routed account proxy pools or endpoint fallback.
- Native WebSocket egress.
- Exact reproduction of Codex HTTP/2 SETTINGS, HPACK state, or request cadence.
- Cross-worker connection sharing.

## Decisions

- stdin accepts newline-delimited `request` and `cancel` commands. Every response event includes the caller-generated `request_id`.
- Rust owns one process-wide reqwest client map keyed by proxy URL and connect timeout. Request-specific total timeouts remain on the request builder.
- Rust spawns one task per request and serializes stdout writes. HTTP/2 multiplexing remains owned by reqwest/hyper.
- Python owns one bounded event queue per request and a single reader task that demultiplexes helper output. A response is still single-consumer.
- Closing a response sends `cancel`, awaits that request's `cancelled`, `end`, or
  `error` acknowledgement (bounded by the cancellation timeout), and only then
  unregisters its owned queue. It does not terminate the shared helper or
  affect another active request; the concurrent-response regression covers
  this ordering. Closing the client closes stdin, terminates if needed, awaits
  the process and reader, and fails remaining requests.
- If process startup fails before a command write, the request raises `NativeEgressUnavailable`. Broken pipes, malformed output, EOF, and helper exit after command dispatch are transport/protocol failures and never trigger aiohttp POST replay.
- A dead helper may be started again only when a later caller submits a new request. In-flight requests from the old generation are never replayed.

## Risks / Trade-offs

- [A stalled consumer can block demultiplexing] -> Use bounded queues and a
  non-blocking reader handoff; overflow fails and cancels only that stream while
  the reader continues serving concurrent streams.
- [Process death can race with restart] -> Protect generation startup/teardown with one lock and bind each reader to its exact process generation.
- [Proxy variants fragment pools] -> Key clients only by effective proxy URL and connect timeout; this is intentional because those settings change connector behavior.
- [Long-lived helper leaks at shutdown] -> Add an explicit application-lifespan close and idempotent client close tests.
