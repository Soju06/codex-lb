## Why
Native WebSocket bursts can fill the Python 64-message queue, which clears accepted frames and aborts an active response. The relay also erases native failure provenance.

## What Changes
- Negotiate per-socket receive credits and pause upstream reads until the consumer returns credit.
- Keep commands, cancellation and sibling streams responsive during pauses.
- Preserve phase, close details and received payload byte/event counts in relay errors and logs.

## Impact
Native WebSocket IPC, Rust reader, Python adapter and regression tests. No configuration or retry policy change.
