## Context

The persistent native helper multiplexes HTTP response events through one Python reader task into one `asyncio.Queue` per request. Each HTTP queue currently has `maxsize=64`, and the reader uses `put_nowait`; a burst of helper chunks therefore becomes a terminal `consumer_backpressure` error even when the response consumer is still making progress. Production saw this after first-token delivery on otherwise healthy Codex Desktop streams.

## Goals / Non-Goals

**Goals:**

- Allow native HTTP response streams to buffer any number of helper events without a fixed event-count rejection.
- Preserve request isolation, cancellation, helper restart, and terminal delivery.
- Keep the patch small enough to deploy as an operational hotfix.

**Non-Goals:**

- Change native WebSocket message queue behavior.
- Change the Rust stdio protocol or upstream chunk boundaries.
- Add an operator setting, database field, or transport fallback.

## Decisions

Native HTTP `request()` creates an unbounded `asyncio.Queue`, while `websocket()` continues creating its bounded control-event queue. The shared reader therefore retains `put_nowait` and its existing overflow handling for bounded queues, but an HTTP response can no longer fail only because 64 transport chunks accumulated.

This directly implements the requested removal. Increasing the fixed limit was rejected because any selected event count can still reject a healthy burst. Adding byte-aware flow control was rejected for this hotfix because it changes both sides of the helper protocol and would require a larger lifecycle design.

The regression test will send substantially more than 64 chunks before consuming the response and assert complete body delivery. Existing WebSocket overflow coverage remains unchanged.

## Risks / Trade-offs

- [A stalled HTTP consumer can accumulate memory until cancellation or process limits intervene] → Existing request deadlines and cancellation still terminate abandoned streams; memory telemetry should be watched after rollout.
- [A very large upstream burst can increase per-request memory] → This is an explicit trade-off of removing the event-count cap; a later byte-aware flow-control change can restore a semantic bound without rejecting based on arbitrary chunk count.
- [The generic reader overflow branch remains present] → It is still required for bounded WebSocket control queues and does not apply to unbounded HTTP queues.

## Migration Plan

No data migration is required. Deploy through the existing HA blue/green script and verify public readiness plus the absence of new bounded-event-queue request errors. Rollback restores the prior fixed queue capacity.

## Open Questions

None for this hotfix.
