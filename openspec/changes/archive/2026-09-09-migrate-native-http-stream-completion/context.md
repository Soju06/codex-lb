# Ownership

The first lifecycle slice owns one native HTTP exchange. It does not move shared
WebSocket request matching, retries, settlement, or downstream delivery into the
worker. Those require their own domain boundaries.

For example, an upstream can send `response.completed` and leave its chunked body
open indefinitely. Rust must send the entire terminal event and drop that body;
Python must not send `cancel` just to finish a successfully completed exchange.
Events after that terminal, even in the same read, are ignored just as the
existing public Python consumer ignores them. A marker only on the last fragment
prevents partial terminal delivery from looking complete.
The false marker is omitted from ordinary fragments so this change adds no
per-delta IPC bytes; only the final terminal fragment carries the new marker.

Bare errors and events requiring context-dependent normalization may still end
through Python cancellation. Uninterpreted SSE, compact collection, raw HTTP, and
persistent WebSockets retain their existing protocols. Application admission,
retry eligibility, usage settlement, and public error mapping stay in Python.
