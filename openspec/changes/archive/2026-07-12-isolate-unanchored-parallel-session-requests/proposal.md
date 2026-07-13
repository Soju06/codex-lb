# Change: Isolate unanchored parallel session requests

## Why

Codex can start foreground and background Responses requests concurrently while
reusing one process-level session header and prompt-cache key.  The HTTP bridge
currently maps that header to one upstream websocket, whose response-create gate
allows only one active request.  Long foreground turns therefore make unrelated
background requests time out locally, and reuse also overwrites the session's
model metadata while the foreground request is still active.

## What Changes

- Give an unanchored request a server-scoped bridge lane when the shared
  session is creating, already serving a visible request, or belongs to another
  model class.
- Reserve an idle canonical bridge across the lookup-to-submit boundary.
- Preserve the origin request's unanchored status through signed owner forwarding and cancellation.
- Keep `previous_response_id` and turn-state requests on their hard continuity
  session.
- Keep durable aliases derived from a forked lane hard owner- and account-bound.
- Preserve normal idle same-model session reuse.
- Add regression coverage for one foreground turn plus multiple concurrent
  background requests sharing the same session header.

## Impact

- Affected spec: `sticky-session-operations`.
- Affected code: `app/modules/proxy/_service/http_bridge/mixin.py`.
- Independent requests no longer share a response-create gate or mutate the
  foreground bridge's model metadata.
