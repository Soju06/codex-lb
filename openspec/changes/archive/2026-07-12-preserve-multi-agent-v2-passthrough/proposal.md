# Preserve Multi-Agent v2 Passthrough

## Summary

Preserve request-scoped Codex multi-agent metadata, compact affinity, and
namespaced tool-call identity across the Responses proxy.

## Motivation

Codex multi-agent v2 uses per-request subagent, parent-thread, and window
metadata; it replays `x-codex-turn-state` on remote compaction; and it emits
tools such as `collaboration.spawn_agent` as namespaced calls with stable call
IDs.

Three proxy boundaries currently lose those distinctions:

- The HTTP-to-WebSocket bridge promotes only turn metadata into each
  `response.create` frame, leaving other compatibility headers tied to the
  reused socket handshake.
- Compact affinity ignores `x-codex-turn-state` and can select an account using
  a less-specific session or prompt-cache key.
- Side-effect deduplication omits the tool namespace and discards call IDs for
  non-code-mode cross-response and history keys, collapsing legitimate
  namespaced calls.

## Scope

- Project subagent, parent-thread, window, and turn metadata into each upstream
  WebSocket request without overriding canonical body metadata.
- Give compact requests the same turn-state affinity precedence as Responses
  requests.
- Include namespace and stable call ID in namespaced side-effect replay
  identity while retaining legacy flat-tool replay protection.
- Add focused unit and integration coverage for reused bridge sessions,
  compact account pinning, and namespaced replay/history handling.

## Impact

- Affected spec: `responses-api-compat`.
- Affected code: Responses WebSocket frame preparation, compact affinity, and
  side-effect tool-call deduplication.
- No database, API schema, or configuration migration is required.
