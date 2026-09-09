# Fix disconnect cleanup and terminal settlement

Streaming and source-chat disconnect cleanup must complete even while Starlette/anyio is cancelling the request task. A completed terminal Responses event must remain authoritative after a later downstream disconnect.

This change hardens database/session teardown, source-chat reservation and request-log cleanup, and Responses stream settlement classification.
