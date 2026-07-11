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

## Impact

- Direct client-facing Responses WebSocket lifecycle only.
- Existing HTTP bridge durable-anchor and full-resend behavior is unchanged.
- No model catalog, database, credential, or HTTP streaming change.

