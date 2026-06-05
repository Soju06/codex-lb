## Why

Live validation on server 113 reproduced a `/v1/responses` hang where
`/v1/chat/completions` and websocket/backend routes kept working, while HTTP
Responses requests logged API-key policy enforcement and then never reached
HTTP bridge account selection or bridge create/reuse logging. Restarting the
process clears the condition, pointing to in-memory HTTP bridge session
lifecycle state rather than upstream model availability.

The current bridge lifecycle can prune and close stale sessions while holding
the global HTTP bridge session lock. Those cleanup paths await per-session
pending locks, upstream-reader cancellation, websocket close, durable session
release, and account lease release. A single wedged stale/pending session can
therefore block unrelated new `/v1/responses` HTTP bridge requests before they
reach account selection.

## What Changes

- Refactor HTTP bridge session pruning so the global bridge lock only mutates
  in-memory maps and collects sessions to close; potentially blocking cleanup
  runs outside that lock.
- Keep stale-session pending inspection bounded so a wedged per-session lock
  cannot block unrelated bridge session creation indefinitely.
- Ensure stale-session close paths are best-effort and bounded enough that
  new HTTP Responses work can either create/reuse a bridge session or fail
  locally with an explicit overload/continuity error instead of silently
  hanging.
- Add regression coverage for stale/pending bridge sessions that previously
  could block fresh `/v1/responses` bridge startup before account selection.

## Impact

- `/v1/responses` HTTP bridge traffic becomes resilient to stale local bridge
  session state without changing public Responses request/response schemas.
- Existing direct chat-completions and websocket behavior is preserved.
- Stale bridge sessions may be closed asynchronously/best-effort rather than
  synchronously under the session map lock.
