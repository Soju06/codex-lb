## Why

The native WebSocket adapter aborts otherwise healthy streams after only 64 queued messages (#2594). A short consumer scheduling delay can therefore discard valid response frames.

## What Changes

- Reuse the native event queue's byte accounting for WebSocket messages with a 4096-event and 32 MiB payload budget.
- Preserve nonblocking acknowledgement processing and isolate genuinely overflowing connections.
- Classify local buffer overflow as account-neutral; do not add replay or change upstream framing.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `responses-api-compat`: bounded native WebSocket burst delivery.

## Impact

Python native adapter, transport error classification and regression tests. No settings, schema, Rust protocol or idle/reconnect behavior changes. Extends the existing stream-buffer policy from #2173 to the second, WebSocket-specific receive queue.
