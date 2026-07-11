# Context

A turn state supplied by a client represents a specific conversation turn and
rightly outranks a broader session header. A turn state synthesized by the
proxy solely because the current WebSocket handshake lacked one has different
semantics: it is an upstream continuity header, not a durable client routing
choice.

The implementation carries the exact synthesized value only for the active
connection. Consequently, if a client receives that value in the handshake
response and sends it on its next connection, it is again treated as a
client-supplied continuation key.

If the first handshake has no accepted session header, its generated turn
state is also the continuity-cache key. This preserves the prior completed
response anchor for a later client-echoed reconnect without changing the
first handshake's routing precedence.

Example: two backend WebSocket connections both send `session_id: session-a`
without a turn-state header. The proxy may forward a different `turn_*` value
on each connection, but both account selections use `session-a`.
