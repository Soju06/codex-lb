## Why

The native HTTP adapter aborts otherwise healthy Responses streams when more than 64 helper chunks accumulate between consumer turns. Real Codex Desktop traffic is hitting this implementation limit after first-token delivery, even though upstream, the application, and the downstream connection remain healthy.

## What Changes

- Remove the fixed event-count capacity from each native HTTP response queue.
- Stop manufacturing `consumer_backpressure` failures solely because a native HTTP stream has more than 64 pending helper events.
- Keep native WebSocket message buffering and its existing bounded overflow behavior unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: Native HTTP response delivery no longer imposes a fixed per-stream event-count queue limit.

## Impact

The Python native-egress adapter, its queue-overflow regression tests, and the native HTTP client contract are affected. Public request and response schemas, the Rust helper protocol, database state, and WebSocket buffering are unchanged.
