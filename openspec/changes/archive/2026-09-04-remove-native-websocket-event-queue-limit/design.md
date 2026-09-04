## Context

The persistent native helper multiplexes HTTP and WebSocket protocol events through one Python reader task. HTTP response queues no longer have an event-count cap, but `SubprocessNativeEgressClient.websocket()` still creates its per-request transport-event queue with `maxsize=64`. The reader uses `put_nowait`; a burst of frames or send acknowledgements can therefore be turned into a `consumer_backpressure` failure before the WebSocket relay has a chance to drain the queue. The relay then reports the generic incomplete-stream contract to the downstream client.

## Goals / Non-Goals

**Goals:**

- Allow native WebSocket transport events to buffer bursts without a fixed 64-event rejection.
- Preserve event ordering, request isolation, cancellation, helper restart, and terminal failure delivery.
- Keep application-message backpressure unchanged: the queue exposed by `NativeEgressWebSocket.receive()` remains bounded.
- Keep the patch limited to the Python native-egress adapter and focused regression coverage.

**Non-Goals:**

- Do not make the application-message queue unbounded.
- Do not change the Rust stdio protocol, WebSocket frame limits, ping/pong watchdogs, or downstream relay semantics.
- Do not add an operator setting, database field, or transport fallback.

## Decisions

`SubprocessNativeEgressClient.websocket()` will create an unbounded `asyncio.Queue()` for helper transport events, matching the HTTP request path. The shared reader will retain `put_nowait` and its generic overflow branch for compatibility with any future bounded stream; this WebSocket queue can no longer raise `QueueFull` because of an event count. The separate `_messages` queue remains `maxsize=64`, so a consumer that genuinely stops draining application payloads still fails closed and releases the native request.

The regression helper will emit substantially more than 64 ordered WebSocket events after handshake and the test will drain them, asserting that every frame arrives and no `consumer_backpressure` error is synthesized. A second assertion will keep the application-message queue's existing bound visible so this change cannot silently remove its memory guard.

Raising the fixed limit was rejected because any finite value can reject a legitimate burst. Making both queues unbounded was rejected because application payloads are controlled by the remote peer and require an explicit stalled-consumer guard. Changing the reader to await `Queue.put()` was rejected because one slow request would then block demultiplexing for every native operation in that helper generation.

## Risks / Trade-offs

- [A stalled WebSocket can accumulate transport events until the application-message guard or cancellation runs] → Transport events are small control/frame envelopes, request deadlines and socket cancellation remain active, and application messages retain the existing bounded queue.
- [The generic reader overflow branch remains reachable only for other bounded queues] → Keep the branch and its terminal cleanup unchanged so future bounded streams fail closed rather than wedging the shared reader.
- [A burst can increase short-lived per-connection memory] → This removes an arbitrary event-count failure while retaining the 24 MiB event-line limit and the 64-message consumer guard.

## Migration Plan

No data migration is required. Run focused native-egress tests and strict OpenSpec validation. If deployed, use the existing HA blue/green rollout; rollback restores the prior WebSocket event queue capacity without changing the helper protocol.

## Open Questions

None for this focused fix.
