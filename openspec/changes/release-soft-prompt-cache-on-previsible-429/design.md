## Context

`_stream_with_retry` classifies a pre-visible HTTP 429 as `rate_limit` and
takes `failover_next`: it records keyed health, releases the stream lease,
excludes the account, and loops. Soft `prompt_cache_key` affinity is still
passed into the next `_select_account_with_budget_compatible` call with
`reallocate_sticky=False`. Prompt-cache selection then prefers the excluded
owner and returns no eligible accounts (#1924).

Inline images force HTTP upstream transport, which is how the reporter hit
this hole. The same `failover_next` site also runs for other pre-visible
rate-limit/quota/transient classes; the release rule is the sticky policy,
not an image-specific branch.

Confirmed hole: `app/modules/proxy/_service/streaming/retry.py` first-event
and post-refresh `action == "failover_next"` blocks. Adjacent pre-dispatch
connect failure already does `affinity = replace(affinity, reallocate_sticky=True)`
after the account is excluded.

## Goals / Non-Goals

**Goals:**

- After a pre-visible `failover_next` exclusion, release soft sticky affinity
  so the next selection can pick another eligible account.
- Keep file-pin, turn-state, and other required-owner requests fail-closed.
- Prove the product path (`stream_responses` with inline image +
  `prompt_cache_key`) and the file-pin counterpart.

**Non-Goals:**

- New `CODEX_LB_*` settings.
- Changing post-visible replay, compact, or HTTP-bridge ownership rules.
- Probe / warmup / #1950 work.
- Rebinding hard `CODEX_SESSION` rows under budget-pressure semantics.

## Decisions

1. **Reuse `reallocate_sticky=True`.** Same lever as the adjacent pre-visible
   failover sites. Do not invent a parallel "clear sticky key" path.

2. **Gate on required ownership, not on "has an image".** Inline image is the
   reporter's trigger, not a distinct ownership class. File pins, turn-state,
   and `require_preferred_account` stay pinned. Soft prompt-cache without those
   owners may reallocate.

3. **Apply at both `failover_next` sites** (first-event and post-refresh).
   They share the hole; fixing only one would leave the same 429 after a
   same-account refresh replay.

4. **`_move_verified_fresh_replay_from_owner` still owns the verified-replay
   case.** That helper already sets `reallocate_sticky=True` when it succeeds.
   The new release runs only when that move does not apply.

## Risks / Trade-offs

- [Soft session-header locality also reallocates on 429] → Accepted and
  consistent with the pre-dispatch path's `not require_preferred_account`
  gate. Hard required owners remain fail-closed.
- [Reallocation abandons a warm cache] → Mitigation: the warm account just
  429'd; keeping the pin fails the request. TTL still expires unused keys.
- [Health write before reservation settle] → Existing `_handle_or_defer_keyed_stream_health`
  + lease release order is unchanged.

## Migration Plan

- No schema or settings migration.
- Deploy is a retry-policy fix; no operator action.

## Open Questions

- None. Ownership vs soft-cache release is already specified for adjacent
  pre-visible paths.
