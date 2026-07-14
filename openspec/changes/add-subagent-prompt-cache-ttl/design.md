## Context

OpenCode sends subagent requests with `x-parent-session-id`. Tool calls are sent as ordinary conversation requests and are not a reliable lifecycle category for this feature.

Subagents have independent bridge sessions and do not need parent-style prompt-cache affinity by default. Retaining a mapping is optional and must not retain the runtime bridge session or its account stream lease.

## Goals

- Detect true subagents from `x-parent-session-id`.
- Default subagents to No Cache.
- Allow operators to retain subagent PROMPT_CACHE mappings for a positive number of seconds.
- Close subagent bridge sessions immediately after the response stream ends.
- Preserve canonical parent-session behavior.

## Decisions

1. The dashboard field `http_responses_session_bridge_subagent_prompt_cache_ttl_seconds` is nullable. `NULL` or `0` means No Cache (immediate release); a positive value is the subagent bridge session, stream lease, and mapping retention TTL.
2. No-Cache subagents pass no sticky key to account selection, so the load balancer neither reads nor writes a PROMPT_CACHE mapping.
3. Enabled subagent affinity passes the subagent TTL as `sticky_max_age_seconds`; the bridge session and stream lease are retained for the TTL duration, then released via a delayed background close task.
4. The parent mapping is never deleted by subagent cleanup. A stale mapping for a No-Cache subagent may be deleted as a cleanup safeguard.
5. Sticky mappings persist an `is_subagent` marker so the reaper can apply the subagent TTL without deleting parent mappings.

## Non-Goals

- No active bridge-session API or dashboard table.
- No change to OpenCode tool-call concurrency.
- No change to canonical parent prompt-cache affinity.
