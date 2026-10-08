## Context

See `proposal.md` for motivation. Current Codex requests provide exact `thread-id`, broader process-session metadata, and child lineage headers. Exact child threads currently seed from the broader process mapping, which places parent and children together. Hard response continuity is resolved separately and must remain fail-closed.

## Goals / Non-Goals

**Goals:**

- Prefer an alternate eligible account only for a fresh, account-neutral child.
- Make `parent_bound_only` depend on positive exact-parent previous-response evidence.
- Keep selection fallback, child stickiness, and all hard ownership rules intact.

**Non-Goals:**

- Moving existing child threads or recovering broken upstream response chains.
- Load balancing every subagent request independently.
- Treating process/session co-membership as hard parent ownership.

## Decisions

### Store a derived parent response-binding marker in a reserved sticky namespace

When the preference is enabled and a request with an exact thread and nonblank `previous_response_id` resolves an account, persist an internal, one-way-derived marker mapped to that account. With the preference `off`, no marker is written, so the default adds no write to any request. The marker never contains raw thread or response IDs. This provides cross-replica evidence without adding a second ownership database. A soft exact-thread mapping alone is deliberately insufficient.

Markers live under the reserved `"\ncodex_subagent_lineage:"` key prefix, next to the existing reserved Live-call namespace. They are routing hints rather than session ownership, so they stay out of the dashboard sticky-session list and filtered deletes, the stale-hard `codex_session` tombstone purge, the hard-owner outage grace refresh, and fleet sticky counts. The periodic sticky cleanup deletes them in bounded batches once they are older than the prompt-cache affinity TTL, and lookups ignore markers older than that TTL.

Alternatives considered: a new `sticky_session_kind` enum value would need a PostgreSQL enum migration and a dashboard kind filter for an internal row type; storing markers as ordinary `codex_session` rows made them appear as hard sessions and enter the tombstone purge.

Alternatives considered: querying request logs would couple correctness to optional retention and logged metadata; inspecting only live bridge sessions would fail across restarts and replicas; treating the parent header as proof would activate the conditional mode without response continuity.

### Apply exclusion as a preference with one normal fallback

Before a fresh child selection, resolve the parent owner according to the configured mode and temporarily exclude it. If selection finds no account, repeat once without that preference. The child's normal exact-thread mapping is then persisted by existing selection logic. The preference is never added when the child mapping already exists or the request contains account-owned state.

Alternatives considered: modifying strategy weights could still choose the parent and would complicate deterministic strategies; a permanent exclusion would violate the required fallback.

### Persist one explicit three-mode dashboard setting

The database and settings API store `off`, `parent_bound_only`, or `always`, with `off` as the migration and model default. No environment variable is added. This keeps existing deployments unchanged and makes the safety boundary visible.

## Risks / Trade-offs

- [A failed upstream continuation can still leave positive routing evidence] → Record only after an owner was resolved and selected; the marker is a diversification condition, not ownership used to route continuations.
- [Two concurrent first child turns can race] → Existing sticky insert/update authority remains the final child-affinity arbiter; the feature never bypasses that persistence.
- [The preferred attempt may have no alternate] → Retry exactly once through ordinary selection with no parent exclusion.
- [Old markers can outlive active work] → Markers expire on the prompt-cache affinity TTL through their own bounded cleanup sweep, and stale markers are ignored at lookup time.

## Migration Plan

Add the non-null setting with server default `off`, deploy backend and frontend together, and leave all existing behavior disabled. Rollback ignores the column; a later downgrade may remove it without altering affinity rows.
