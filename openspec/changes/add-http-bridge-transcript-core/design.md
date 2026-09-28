## Context

Transcript capture and recovery are being delivered as separate stacks.  The
schema must therefore be safe to deploy before either writer or reader is
enabled, and helper behavior must be deterministic before it is wired into a
request path.

## Decisions

- Use nullable JSON text for snapshots and non-null Boolean/integer markers
  with conservative false/zero defaults for existing operations.
- Keep transcript fields on the operation row so a later reader can validate
  one operation atomically.
- Use `(session_id, state, created_at)` for bounded session recovery scans and
  `(response_id, state)` for response-anchor lookups.
- Require stable IDs for repeatable non-tool output items; permit an
  identity-less `compaction` boundary marker.  Tool calls require both item
  and call identities, while tool outputs require their call identity.
- Treat conflicting explicit statuses or conflicting duplicate tool content as
  non-matches so callers can fail closed.

## Non-goals

- No capture, persistence writes, or replay reads are wired in this change.
- No public Responses lifecycle validation or recovery flag changes.
- No edits to archived OpenSpec records.
