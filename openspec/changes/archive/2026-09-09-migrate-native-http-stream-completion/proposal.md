# Move native HTTP stream completion into Rust

## Why

Native HTTP Responses streams currently send their terminal SSE block to Python
and continue reading until Python cancels the helper request. Move completion of
recognized response terminals into the Rust transport, preserving the existing
Python error normalization and fallback paths.

## What Changes

Rust stops after `response.completed`, `response.failed`, or
`response.incomplete`, flushes all fragments of that event, and releases the HTTP
body without waiting for upstream EOF. A negotiated final-fragment marker lets
Python retire the request without a cancellation round trip. WebSocket sessions,
account policy, and context-dependent error completion remain separate owners.
