## Why
An empty WebSocket close frame becomes code 0 in aiohttp. Forwarding that value causes a protocol error for the receiving peer.

## What Changes
- Normalize omitted close status to normal closure 1000.
- Cover the relay WebSocket route with a real empty close frame.

## Impact
Only Desktop relay close-frame forwarding changes. Explicit close codes and reasons remain intact.
