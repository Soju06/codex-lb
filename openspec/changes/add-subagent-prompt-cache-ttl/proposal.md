## Why

OpenCode identifies short-lived child work with the `x-parent-session-id` request header, but codex-lb currently retains its prompt-cache mapping under the same long-lived settings used by parent sessions. The retained mapping can keep subagent affinity visible after the child has finished.

## What Changes

- Add a persisted dashboard setting, `http_responses_session_bridge_subagent_prompt_cache_ttl_seconds`, defaulting to `NULL`.
- Treat a request carrying `x-parent-session-id` as a subagent session.
- Use `NULL` as No Cache; use a positive setting to retain only the subagent PROMPT_CACHE mapping for that duration.
- Close the subagent HTTP bridge immediately after its response stream ends, independent of mapping retention.
- Preserve existing parent-session affinity and HTTP bridge TTL behavior.
- Add a Routing Settings control labelled `Subagent prompt-cache TTL` with validation and immediate settings API persistence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: distinguish optional subagent prompt-cache affinity from parent prompt-cache affinity.
- `frontend-architecture`: expose the persisted subagent prompt-cache TTL in Routing Settings.

## Impact

- Dashboard settings persistence, migrations, settings API schemas, cache invalidation, and frontend settings types.
- HTTP Responses bridge affinity classification and sticky mapping persistence.
- No new client header or upstream protocol is introduced; the change consumes OpenCode's existing `x-parent-session-id` metadata header.
