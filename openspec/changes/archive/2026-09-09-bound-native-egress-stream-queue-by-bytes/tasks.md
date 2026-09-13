# Tasks

- [x] 1.1 `_BoundedEventQueue` (event cap + queued-bytes budget, bytes released on get) and `_new_stream_queue()`; HTTP/SSE and WebSocket connect queues use it.
- [x] 1.2 Tests: byte/event trip and release semantics; a 2000-event burst buffered in the helper pipe drains without failing the consumer; the existing overflow test trips on the byte budget (48 x 1 MiB) and client close still does not hang.
- [x] 1.3 ruff, ty, strict OpenSpec validation.
- [x] 1.4 Local codex review P2 x2: the incoming event counts toward the budget (a queue just under budget rejects an event that would carry it past; an event at an empty queue is always accepted); SSE `text` payloads are measured in UTF-8 bytes.
