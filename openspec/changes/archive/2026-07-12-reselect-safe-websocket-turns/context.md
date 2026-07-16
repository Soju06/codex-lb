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

For a transport change from direct WebSocket to HTTP, the same process may use
the session-scoped WebSocket continuity index to verify that the HTTP input
starts with the exact input fingerprint stored for the referenced response.
The request must also contain matching tool calls before tool outputs and no
account-scoped file id. If any proof is missing, the continuation stays
owner-bound and fails closed when its owner is unavailable. This proof is
process-local; restart or cross-replica gaps deliberately degrade to
fail-closed behavior rather than structural guessing.
