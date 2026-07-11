# Safe direct-WebSocket account switching

## Replay boundary

The proxy can prove that an unanchored body is equivalent only when it injected
the anchor itself after matching locally retained conversation fingerprints.
A client-owned `previous_response_id` plus a long input list is not sufficient
proof that the list is the complete transcript. Such requests retain their
anchor and owner even if a structural helper considers them self-contained for
the narrower `previous_response_not_found` recovery contract.

Owner-pinned requests already on their owner socket are not passed through
per-turn selection again. This prevents temporary rate-limit or health
exclusions from killing a healthy continuation. If the required owner differs
from the open socket, the socket is retired and the unchanged request is
reconnected to that owner.

