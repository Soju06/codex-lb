# Bound the Native Egress Stream Queue by Bytes

## Why

The native egress helper streams every in-flight request's events through one stdout pipe; a single Python reader task fans them out into a per-request queue that the request's consumer drains. That queue was capped at 64 *events*. Framed SSE deltas are tens of bytes each and arrive in bursts of hundreds when upstream flushes reasoning output, and a non-SSE body such as an image-generation JSON is a handful of large chunks -- so on a saturated event loop a perfectly healthy consumer that is merely late for one scheduling turn hit the cap. The reader then failed the request with `native stream consumer exceeded the bounded event queue` (`consumer_backpressure`), which is not a retryable code and reached clients as a 502 / a truncated SSE body ("error decoding response body"). On soju07 this went from 6 to 205 requests a day when the host saturated (#2167): 183 in a single hour, gpt-6-astra and `/v1/images/generations` included.

## What Changes

- The per-request stream queue becomes a **byte budget** (32 MiB of queued payload) with a generous event cap (4096) instead of a 64-event cap. The reader's existing `QueueFull` handling is unchanged: only a consumer that truly stops draining (dead client) trips the bound and is failed so it cannot hold the shared reader hostage.
- Both HTTP/SSE stream queues and WebSocket connect event queues use the new bound; the WebSocket message queue is unchanged (never tripped in production).

## Capabilities

### Modified Capabilities

- `outbound-http-clients`: native helper per-request event queue bound.

## Impact

- `app/core/clients/native_egress.py` (`_BoundedEventQueue`, `_new_stream_queue`, limits); tests in `tests/unit/test_native_egress.py` (byte-budget unit test, 2000-event burst drains without failure, overflow test moved onto the byte budget).
- Worst-case queued memory per request rises from 64 events to 32 MiB; queued bytes are released as the consumer drains. No settings, schema or API changes.

Fixes #2167 (queue bound); the second ask in #2167 (retrying a pre-output overflow) is unchanged: with the byte budget the overflow only fires for a consumer that stopped draining, where a retry would not help.
