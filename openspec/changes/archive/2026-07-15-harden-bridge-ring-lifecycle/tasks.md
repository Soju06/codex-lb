# Tasks — harden-bridge-ring-lifecycle

## 1. Fenced durable lease writes

- [x] 1.1 Rewrite `DurableBridgeRepository.renew_session` as a single fenced
      `UPDATE ... WHERE id AND owner_instance_id AND owner_epoch ... RETURNING`
      statement; fenced-out callers mutate nothing and receive the current
      owner snapshot.
- [x] 1.2 Rewrite `DurableBridgeRepository.release_session` the same way.
- [x] 1.3 Add `DurableBridgeRepository.get_sessions_by_ids` and a
      `DurableBridgeSessionCoordinator.lookup_sessions` passthrough for the
      reconciliation sweep.

## 2. Losing-replica eviction

- [x] 2.1 `_refresh_durable_http_bridge_session`: on fenced-out renewal,
      detach + schedule close of the local session and raise the retryable
      `409 bridge_instance_mismatch` instead of adopting the new epoch.
- [x] 2.2 `_persist_http_bridge_turn_state_alias` /
      `_persist_http_bridge_previous_response_alias`: on fenced-out alias
      write (with unchanged local epoch), detach + schedule close of the local
      session in addition to the existing alias rollback.
- [x] 2.3 Add `reconcile_durable_http_bridge_ownership()` to the bridge mixin
      and invoke it from the ring-heartbeat loop in `app/main.py` every
      heartbeat tick.

## 3. Orphan purge

- [x] 3.1 Add `DurableBridgeRepository.purge_abandoned_before(cutoff)` for
      expired-lease ACTIVE/DRAINING rows (aliases deleted in the same batch).
- [x] 3.2 Add `RingMembershipService.purge_stale_before(cutoff)` and the 24h
      `RING_MEMBER_RETENTION_SECONDS` constant.
- [x] 3.3 Invoke both purges from `StickySessionCleanupScheduler._cleanup_once`.
- [x] 3.4 Gate the abandoned-row purge cutoff on the longest bridge reuse
      window (max of prompt-cache affinity max age, prompt-cache idle TTL,
      codex idle TTL, base idle TTL) so in-reuse-window sessions keep their
      durable rows.
- [x] 3.5 Chunk `get_sessions_by_ids` so reconciliation candidate sets larger
      than the database bind-parameter limit still resolve.

## 4. Post-shutdown grace turn-state takeover

- [x] 4.1 Add helpers classifying a `bridge_owner_unreachable` forward failure
      for a turn-state-anchored request and deciding takeover from a fresh
      durable lookup (no active lease / missing → takeover; live lease → fail
      closed, including DRAINING rows whose lease is still live).
- [x] 4.2 Wire the takeover retry into the owner-forward failure handler in
      `streaming.py`, reusing the request-routing lookup semantics (including
      the latest-turn-state fallback) for the freshness check.

## 5. Specs and validation

- [x] 5.1 Add `bridge-ring-membership` capability delta spec.
- [x] 5.2 Add fencing/eviction/purge requirements to
      `sticky-session-operations` delta spec.
- [x] 5.3 Add the post-grace turn-state recovery scenario to
      `responses-api-compat` delta spec.
- [x] 5.4 `openspec validate harden-bridge-ring-lifecycle` passes.

## 6. Tests

- [x] 6.1 Repository fencing tests (two sessions over one DB): stale-epoch
      renew/release mutate nothing and report the current owner.
- [x] 6.2 Service-level eviction tests: fenced-out renewal and fenced-out
      alias writes close the local session (upstream websocket closed,
      account lease released) instead of adopting the new epoch; the
      reconciliation sweep closes fenced-out sessions and leaves owned ones.
- [x] 6.3 Purge tests: abandoned ACTIVE/DRAINING rows and their aliases are
      purged past the cutoff while live-lease rows survive; stale ring members
      older than 24h are purged; the cleanup scheduler invokes both purges.
- [x] 6.4 Turn-state takeover tests at the streaming product path: forward
      failure with a released/expired durable lease recovers locally through
      the routing-semantics lookup; a live-lease owner still fails closed with
      the retryable 503, including DRAINING rows with a live lease.
- [x] 6.5 `uv run ruff check app tests` and `uv run ruff format --check app
      tests` pass; targeted pytest selection passes.
