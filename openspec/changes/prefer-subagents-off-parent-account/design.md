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

### Store a derived parent response-binding marker in existing sticky storage

When a request with an exact thread and nonblank `previous_response_id` resolves an account, persist an internal, one-way-derived marker mapped to that account. The marker uses the existing sticky lifecycle and never contains raw thread or response IDs. This provides cross-replica evidence without adding a second ownership database. A soft exact-thread mapping alone is deliberately insufficient.

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
- [Old markers can outlive active work] → Reuse the established sticky-session lifecycle and derived key namespace, with no raw identifiers.

## Migration Plan

Add the non-null setting with server default `off`, deploy backend and frontend together, and leave all existing behavior disabled. Rollback ignores the column; a later downgrade may remove it without altering affinity rows.
