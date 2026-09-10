# Design

`_release_websocket_response_create_gate` keeps its existing state-clearing and
gate-release ordering, but awaits the captured account lease release through
`asyncio.shield`. The release operation therefore continues after cancellation
of the surrounding WebSocket task, returning the account slot without changing
the existing response-create gate semantics.
