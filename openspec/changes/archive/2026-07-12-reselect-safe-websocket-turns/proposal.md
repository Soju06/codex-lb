# Reselect safe direct-WebSocket turns

## Why

A client-facing Responses WebSocket can remain open across multiple turns.
The account selected for an earlier turn may no longer support the next turn's
model or tier, or may have become unavailable. Fresh turns can safely reconnect
through another account, but a client-owned `previous_response_id` continuation
must remain on its owner and must not be failed merely because a transient
selector check excludes an otherwise healthy open owner socket.

## What changes

- Revalidate an open direct-WebSocket account only for an unsent request that
  can safely move accounts.
- Reconnect owner-pinned continuations when the requested owner differs from
  the currently open socket, without removing their anchor.
- Allow account switching for a previous-response turn only when the anchor was
  proxy-injected from verified local continuity and an equivalent fresh body
  was retained.
- Keep client-owned previous-response continuations owner-bound for quota and
  other pre-visible failures.
- Allow a WebSocket-to-HTTP full resend to move accounts only when the shared
  session's in-memory continuity fingerprint proves the resent prefix and the
  request contains no account-scoped file reference.
- Carry the same verified replay boundary through durable HTTP bridge quota
  recovery, excluding the failed owner and releasing its account-local lease.
- Let file-free unanchored HTTP bridge retries exclude an account that stalled
  before response creation, while keeping file-backed retries owner-bound.
- Prevent account-scoped file IDs, client-owned security retries, and retired-
  account turn-state from crossing account boundaries.

## Impact

- Direct client-facing Responses WebSocket lifecycle and verified
  WebSocket-to-HTTP full-resend recovery.
- Existing HTTP bridge durable-anchor behavior is unchanged; safe unanchored
  pre-created retries may move away from a stalled account.
- No model catalog, database, credential, or HTTP streaming change.
