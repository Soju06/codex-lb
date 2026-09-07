## Why

Native WebSocket events are still routed through a per-connection `asyncio.Queue(maxsize=64)` even though the HTTP stream queue was made unbounded. A burst of upstream frames or acknowledgements can therefore manufacture `consumer_backpressure`, terminate an otherwise healthy socket, and surface as `stream_incomplete` while the downstream relay is still able to make progress.

## What Changes

- Remove the fixed event-count capacity from the native WebSocket transport-event queue.
- Preserve event ordering, request isolation, cancellation, helper-generation failure handling, and terminal delivery.
- Keep the separate application-message queue bounded at its existing limit so a stalled downstream consumer still fails closed instead of accumulating unbounded payloads.
- Add a regression covering a native WebSocket event burst larger than 64 events.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: Native WebSocket transport-event delivery no longer rejects a healthy burst solely because 64 events are pending.

## Impact

The Python native-egress adapter, its focused unit tests, and the outbound HTTP/WebSocket client contract are affected. Public WebSocket frames, close semantics, the Rust helper protocol, application-message backpressure, database state, and deployment topology are unchanged.
