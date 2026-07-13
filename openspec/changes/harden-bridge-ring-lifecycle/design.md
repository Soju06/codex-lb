# Design — harden-bridge-ring-lifecycle

## Context

Multi-replica HTTP bridge ownership is persisted in `http_bridge_sessions`
(owner instance + monotonically increasing `owner_epoch` + lease expiry) with
`bridge_ring_members` providing the replica ring view. `register_owned_alias`
already fences on `(id, owner_instance_id, owner_epoch)` with a single
`UPDATE ... RETURNING`, but `renew_session` and `release_session` were
read-check-then-write, and no lifecycle path ever told a fenced-out replica to
drop its local session, purged abandoned durable rows, or purged dead ring
members.

## Decisions

### 1. Fenced single-statement renew/release

`DurableBridgeRepository.renew_session` and `release_session` become a single
`UPDATE ... WHERE id AND owner_instance_id AND owner_epoch ... RETURNING
<all snapshot columns>` statement, mirroring `register_owned_alias`.

- Matched row → snapshot built directly from the RETURNING row (no ORM
  identity-map interaction, no second round trip).
- No matched row → a follow-up `SELECT` (with `populate_existing`) returns the
  current owner snapshot without mutating anything; a missing row returns
  `None`.
- SQLite vs PostgreSQL: both dialects support `UPDATE ... RETURNING`
  (`purge_closed_before` already relies on DELETE RETURNING for both). On
  PostgreSQL READ COMMITTED the single statement re-evaluates the WHERE
  predicate against the committed row version, eliminating the lost-update
  window; on SQLite the writer lock plus single statement gives the same
  guarantee cross-process.

### 2. Losing-replica eviction

- `_refresh_durable_http_bridge_session` (called with the bridge registry lock
  held on both call sites) no longer adopts a foreign epoch. When the fenced
  renewal reports a different owner instance or epoch, the local session is
  detached from the registry, scheduled for close (upstream websocket +
  account lease release), and the request fails with the existing retryable
  `409 bridge_instance_mismatch` envelope — the same contract as a rejected
  durable claim.
- `_persist_http_bridge_turn_state_alias` / `_persist_http_bridge_previous_response_alias`
  extend their existing fence-rejection rollback: when the alias write is
  fenced out and the session's local epoch still equals the epoch used for the
  write (i.e. no concurrent same-session re-claim), the session is detached
  and scheduled for close. Generation guards and same-session epoch-refresh
  behavior are preserved.
- A reconciliation sweep `reconcile_durable_http_bridge_ownership()` is
  piggybacked on the existing 10s ring-heartbeat loop in `app/main.py`. It
  batch-loads durable snapshots (`get_sessions_by_ids`) for local sessions
  whose `last_used_at` is older than the durable lease TTL and closes any
  session whose durable row is owned by another instance/epoch. This bounds
  the orphaned-upstream-websocket window to roughly the lease TTL instead of
  the 900s idle TTL.

### 3. Orphan purge

- `DurableBridgeRepository.purge_abandoned_before(cutoff)` deletes
  ACTIVE/DRAINING rows whose lease is expired (or null) and whose
  `last_seen_at` predates the retention cutoff, deleting aliases in the same
  batch and re-checking the predicate inside the DELETE (same batched shape as
  `purge_closed_before`).
- `RingMembershipService.purge_stale_before(cutoff)` deletes
  `bridge_ring_members` rows whose heartbeat is older than the cutoff; the
  sticky-session cleanup scheduler invokes it with a fixed 24h retention
  (`RING_MEMBER_RETENTION_SECONDS`), far beyond the 30s stale threshold and
  the shutdown stale-aging grace.
- Both purges run from the existing leader-gated
  `StickySessionCleanupScheduler._cleanup_once`. The closed-row purge keeps the
  `openai_cache_affinity_max_age_seconds` cutoff; the abandoned-row purge uses
  a retention of `max(openai_cache_affinity_max_age_seconds,
  prompt-cache idle TTL, codex idle TTL, base idle TTL)` because an idle local
  session stays reusable until its effective idle TTL (default prompt-cache
  TTL is 3600s vs the 1800s affinity max age) and purging its ACTIVE durable
  row earlier would strip a still-reusable session of durable ownership and
  continuity aliases.

### 4. Post-shutdown grace turn-state takeover

On a `bridge_owner_unreachable` forward failure for a turn-state-anchored
request (turn-state header present, no `previous_response_id`), the streaming
path performs a fresh durable lookup using the same request-routing
resolution semantics (`lookup_request_targets`: registered aliases, the
canonical session key, and the latest-turn-state fallback), so a row that
was originally resolved without a registered alias keeps its durable anchor:

- lease released (owner `NULL`), lease expired, row CLOSED, or row missing →
  retry locally with `allow_bootstrap_owner_rebind` semantics and the fresh
  lookup as the durable anchor, so the local claim path can take over.
- live lease held by another instance → keep failing closed with the
  retryable 503, even when the row is DRAINING. Shutdown marks rows DRAINING
  before releasing them, so a draining owner may still be finishing an
  in-flight turn until its lease is released or lapses; taking over earlier
  would split continuity across concurrent owners.

## Deviations from the original design document

The original machine-readable design JSON was lost with the interrupted
session's scratchpad; this design.md was reconstructed from the surviving
`proposal.md` (authored from that design) plus code inspection. Two concrete
choices were made where the proposal left latitude:

- Fenced-out renewals surface as the established retryable
  `409 bridge_instance_mismatch` error (same as claim rejection) rather than a
  new error code, keeping the failure taxonomy stable.
- Alias-write eviction is skipped when the session's local epoch advanced
  concurrently (same-session re-claim), preserving the existing
  epoch-refresh rollback semantics covered by
  `test_durable_alias_fence_rejection_rolls_back_after_same_session_epoch_refresh`.

## Risks

- Eviction closes sessions that previously (unsafely) kept serving; any
  in-flight requests on a fenced-out session receive the retryable
  `stream_incomplete` / `bridge_instance_mismatch` errors instead of riding a
  stolen session.
- The reconciliation sweep adds one batched SELECT per heartbeat only when
  stale-lease local sessions exist.
- No Alembic migration: all changes use existing tables and columns.
