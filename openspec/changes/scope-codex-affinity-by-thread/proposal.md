## Why

Current Codex sends one process `session-id` and one `prompt_cache_key` across
an entire root/subagent tree while giving each logical conversation its own
stable `thread-id`. codex-lb still keys backend account locality, direct
WebSocket replay state, and HTTP bridge lanes primarily from the shared
process/cache identities. Sibling threads therefore overwrite one another's
locality and can reuse replay or bridge history that belongs to another
thread.

## What Changes

- Derive one source-separated bounded locality key from the process session
  and `thread-id` for backend Responses and compact requests.
- Seed a new thread from an eligible process-session preference, then persist
  only the thread-local bounded row so later failover does not move siblings.
- Key direct WebSocket retained state and HTTP bridge canonical lanes by the
  same logical thread identity.
- Route thread-goal operations from their payload `threadId`.
- Preserve explicit turn state, previous response, file, conversation, bridge,
  replay, and legacy raw Codex rows as hard ownership; keep `prompt_cache_key`
  unchanged as the upstream cache hint.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: Scope backend Responses, compact, direct WebSocket,
  HTTP bridge, and thread-goal locality by Codex thread identity.
- `sticky-session-operations`: Keep process preference and legacy hard-owner
  semantics while introducing bounded per-thread locality.

## Impact

The change touches proxy affinity parsing, account selection, direct
WebSocket continuity, HTTP bridge identity, thread-goal routing, and focused
tests. It adds no setting, schema, migration, dashboard surface, or upstream
payload rewrite.
