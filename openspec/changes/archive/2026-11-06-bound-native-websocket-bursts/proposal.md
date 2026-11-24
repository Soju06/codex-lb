## Why
The native helper reader accepts small-event bursts within its 32 MiB / 4096-event budget, but the WebSocket pump forwards those events into a second queue capped at 64 messages. An actively draining relay can therefore fail with consumer_backpressure while handling an ordinary response burst.

## What Changes
- Apply byte and message bounds to the WebSocket delivery queue, matching the helper event queue's burst budget.
- Account for text, interpreted payload copies, binary frames and close reasons, releasing bytes on dequeue.
- Preserve nonblocking shared dispatch, independent overflow cancellation, command acknowledgments and ordered delivery.

## Impact
- Affected spec: outbound-http-clients
- No settings, schemas, routing, retry, account selection or protocol changes.
