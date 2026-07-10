# Change: Isolate unanchored parallel session requests

## Why

Codex can start foreground and background Responses requests concurrently while
reusing one process-level session header and prompt-cache key.  The HTTP bridge
currently maps that header to one upstream websocket, whose response-create gate
allows only one active request.  Long foreground turns therefore make unrelated
background requests time out locally, and reuse also overwrites the session's
model metadata while the foreground request is still active.

## What Changes

- Give an unanchored request a request-scoped soft bridge lane when the shared
  session is creating, already serving a visible request, or belongs to another
  model class.
- Keep `previous_response_id` and turn-state requests on their hard continuity
  session.
- Preserve normal idle same-model session reuse.
- Add regression coverage for one foreground turn plus multiple concurrent
  background requests sharing the same session header.

## Impact

- Affected spec: `sticky-session-operations`.
- Affected code: `app/modules/proxy/_service/http_bridge/mixin.py`.
- Independent requests no longer share a response-create gate or mutate the
  foreground bridge's model metadata.
