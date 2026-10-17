## Context

Upstream Codex deliberately separates three roles: `session-id` identifies a
root process tree, `thread-id` identifies one root/child/fork/resumed thread,
and `prompt_cache_key` defaults to the shared process session to colocate
cache entries. The old codex-lb bridge assumption that explicit cache keys
distinguish children stopped holding when upstream adopted the shared cache
key.

## Decisions

1. **Use a typed thread identity.** Parse process session and `thread-id`
   independently. Derive a versioned, header-inaccessible opaque key from both,
   with a separately namespaced thread-only fallback when no process session
   exists. Never infer identity from subagent or parent markers.
2. **Reuse bounded prompt-cache rows for soft locality.** Thread locality uses
   the existing `prompt_cache` kind and configured freshness window. A missing
   thread row first prefers the eligible source-separated process-session row;
   successful admission persists the selected account under the thread key
   without rewriting the process row. Because current Codex includes
   `thread-id` on its first root request, the first admitted thread also
   initializes a missing process preference with an atomic insert-if-absent.
   Later thread movement can neither overwrite that first-writer default nor
   mutate a sibling row. A provisional recovery-probe reservation persists
   only its reversible thread row; it cannot publish the immutable process
   default until a normal admission does so, because a failed probe CAS cannot
   safely delete a seed that a concurrent sibling may already have observed.
3. **Keep ownership separate from locality.** Raw legacy `codex_session` rows
   are looked up independently and remain hard. Exact turn state, response,
   file, conversation, bridge, replay, and reattach evidence keeps its existing
   precedence and conflict behavior.
4. **Use the same logical identity at transport boundaries.** Direct
   WebSocket retained replay/tool state is count-bounded in memory by thread.
   An HTTP bridge canonical lane is hard while live/durable, but its pre-bridge
   account hint remains soft and bounded.
5. **Migrate bridge lanes only through exact aliases.** With current Codex,
   legacy `(session-id, prompt_cache_key)` is shared by siblings. A request
   carrying `thread-id` must not fall back to that canonical key. Existing
   lanes remain recoverable through exact turn-state or previous-response
   aliases and otherwise expire naturally. Authenticated forwarded affinity
   keys are accepted verbatim and never derived again.
6. **Preserve upstream cache intent.** `prompt_cache_key` is forwarded
   unchanged. Request-log conversation grouping continues using raw
   `thread-id`.

## Rejected Alternatives

- Adapting the marker/TTL/schema design in #1309: thread identity is already
  explicit, so marker inference, a migration, settings, and dashboard controls
  add lifecycle without improving identity.
- Using subagent or parent-thread markers: they describe role/provenance and
  can group siblings rather than identify the current thread.
- Using `prompt_cache_key` or `(session-id, prompt_cache_key)`: current Codex
  intentionally sends the same value for root and children.
- Rewriting `prompt_cache_key` to `thread-id`: this defeats upstream's intended
  tree-wide cache colocation.
- Balancing every unseen thread independently: a thread boundary contains
  justified divergence; it is not a reason to discard the healthy process
  preference on first placement.
- Durable per-thread Codex rows or a new sticky kind/setting: existing bounded
  rows already express soft locality, while bridge/object ownership remains
  durable separately.
- Falling back to the old bridge canonical key when `thread-id` exists: that
  can attach a sibling's history. Only exact hard aliases are safe migration
  evidence.

## Risks / Trade-offs

- Thread rows expire. Selection and active response completion refresh the row
  so a long-lived active thread does not lose reconnect locality solely to the
  freshness window.
- A health, quota, explicit restart, or proven hard-owner transition may still
  move a thread. The fix removes cross-thread collisions; it does not promise
  an account never changes.
- Mixed-version replicas may retain old shared bridge rows. New requests with
  a thread identity ignore those rows unless an exact alias proves ownership,
  allowing safe coexistence until cleanup.
