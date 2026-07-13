# Design: share-account-concurrency-caps

## Provenance

The original machine-readable design artifact for this change (session scratchpad `designs/share-account-concurrency-caps.json`, backed by the verified multi-replica audit findings in `portfolio.json`) was lost when the session scratchpad was wiped between the interrupted first implementation attempt and this one. This document reconstructs the design from (a) the partially implemented code left in the worktree by the first attempt, (b) the 2026-07-12 replica-HA effort notes, and (c) the current code. Any deviation from the lost original is therefore unverifiable; the reconstruction below is treated as authoritative and was reviewed against the code before completion. No intentional deviations from the partial implementation's design were made; gaps that were filled are listed under "Completed in this attempt".

## Context

`LoadBalancer` enforces per-account response-create and stream caps against in-process `RuntimeState` counters (`app/modules/proxy/load_balancer.py`). Caps come from `effective_account_concurrency_caps(dashboard_settings)`, which merges dashboard overrides with process env defaults. Nothing about this is replica-aware: N replicas admit N x cap concurrent work per account. All admission call sites (selection filter `_filter_states_for_account_caps`, `acquire_account_lease`, opportunistic admission, account-cap waits) already funnel through `effective_account_concurrency_caps`, so making that one function replica-aware fixes every path at once.

The bridge ring (`bridge_ring_members` table, `RingMembershipService`) already gives every replica a heartbeat-fresh, identically-sorted view of live instance ids (heartbeat every 10s, stale threshold 30s), and `app/main.py` already runs an unconditional register + heartbeat loop per replica.

## Goals

- Configured account caps behave as cluster-wide per-account targets, not per-replica allowances.
- Zero additional DB reads on the request/admission hot path.
- No new table, no migration, no cross-replica mutable shared state.
- Membership churn must not transiently over-admit (a missed heartbeat must not double every survivor's share).
- Operators can restore the old semantics.

## Non-goals

See proposal. Notably: exact global enforcement (DB-counter or lease-table designs rejected for hot-path DB I/O and settlement complexity), and per-worker sub-partitioning within one pod.

## Decisions

### D1: Deterministic local partitioning from the sorted ring member list

Each replica computes its own share locally: with `R` active members sorted by instance id and this replica at rank `k`, the share is `floor(cap / R) + (1 if k < cap mod R else 0)`, floored at 1 slot. All replicas see the same sorted list, so shares are consistent and sum to the configured cap (when `cap >= R`) without any coordination write. Alternatives rejected:

- Shared DB counters / distributed leases: adds a DB round trip (or more, with settlement) to every admission decision — the exact cost the recent pre-upstream-DB-cut work removed.
- Gossip/broadcast of inflight counts: new infrastructure, unbounded staleness, still approximate.

### D2: Floor shares at one slot

When `cap < R`, ranks beyond the remainder would get 0 slots and the account would be unroutable on those replicas, breaking sticky/affinity routing. Shares are floored at 1, so the aggregate may reach `R` for very small caps. This bounded overshoot (at most `R - cap` extra slots, only when `cap < R`) was judged safer than unroutable accounts. `cap <= 0` remains "unlimited" on every replica.

### D3: Share-growth-aware hysteresis on membership changes

- Hysteresis is keyed on whether *this replica's share* actually grows for a **configured** cap (the response-create and stream limits in effect), not on the member count or a rank-direction heuristic, and not against an arbitrarily large cap. The share `floor(cap / R) + (1 if rank < cap mod R else 0)` (floored at 1) is monotone non-increasing in both `R` and `rank`, but neither direction alone decides growth: a count decrease can be outweighed by a rank increase, and a rank decrease by a large enough count increase. Both direction heuristics (rank-only, or "count-down ⇒ defer") are unsafe: e.g. cap 8 shrinks from `R=6, rank=0` (2 slots) to `R=5, rank=3` (1 slot) even though the count fell, because churn removed members while adding lower-sorting ids — a "count decreased ⇒ defer" rule would wrongly hold the old 2-slot share past the window and over-admit. The decision therefore compares the prospective share against the current share for each configured cap directly, using the same `partition_cap` formula the admission path enforces.
- Some configured cap's prospective share is strictly greater than its current share: defer — adopting could over-admit against the cluster-wide cap. Adopt only after that exact pending partition (count + rank) has been held continuously for `proxy_account_cap_partition_scale_down_seconds` (default 60s, `ge=30`). A single missed heartbeat (stale threshold 30s) recovers within one 10s heartbeat interval, so it never survives the window; a real scale-down is adopted ~60-70s after the last heartbeat. During the window survivors keep their smaller shares (under-admission, safe). Example: cap 8 grows from `R=5, rank=4` (1 slot) to `R=6, rank=0` (2 slots), so it defers despite the larger count.
- Every configured cap's prospective share is less than or equal to its current share: adopt immediately — safe toward upstream — regardless of whether the count or rank rose or fell. This covers a later rank, a count increase that outweighs an earlier rank (cap 8: `R=2, rank=1` → `R=3, rank=0`, 4 → 3 slots), and a count decrease that a rank increase turns into a shrink (cap 8: `R=6, rank=0` → `R=5, rank=3`, 2 → 1 slot).
- Any change of the pending partition — including a different rank at the same count — restarts the window, so only one continuously-held target can be adopted; observing the adopted partition again clears the pending state.

### D4: Fail-closed refresh, self always counted

The partition refreshes from `RingMembershipService.list_active` after ring registration and after every heartbeat tick (`app/main.py` lifespan). A failed membership read logs and retains the last adopted partition — it never falls open to the full configured caps. The observing replica adds its own instance id to the observed set even when its ring row is missing or stale (startup, heartbeat gap), so the degenerate outcome is fewer shared slots, never a crash or an empty ring.

### D5: Scope switch

`proxy_account_caps_scope: Literal["partitioned", "replica"]`, default `partitioned`. `replica` restores legacy per-replica caps (documented escape hatch for deployments that intentionally sized caps per replica). Single-replica deployments are unaffected either way (`replica_count <= 1` short-circuits to the full caps).

### D6: SQLite vs PostgreSQL behavior

Identical by construction. Partition derivation only *reads* `bridge_ring_members` via `list_active` (a plain `SELECT ... WHERE last_heartbeat_at >= cutoff ORDER BY instance_id`), which has no dialect-specific behavior; the dialect-specific upsert paths in `RingMembershipService.register/heartbeat` already exist and are unchanged. SQLite deployments sharing one database file across processes/hosts partition exactly like PostgreSQL deployments; a single-process SQLite deployment sees `replica_count == 1` and keeps full caps. No migration is required on either backend (`bridge_ring_members` exists since the multi-instance bridge work).

### D7: Error message and observability contract

Partitioned-cap rejections keep the stable reasons `account_response_create_cap` / `account_stream_cap` and the local-overload envelope; only the human-readable message gains "this replica's share is S of the per-account limit C across R replicas" when `R > 1`, so operators do not misread a share-exhausted rejection as a misconfigured cap. New gauge `codex_lb_cap_partition_replicas` (multiprocess mode `livemax`: sibling workers share one instance identity and compute the same count, so a max across live workers reports the real value; plain `max` would keep a dead worker's stale higher count after a scale-down because `mark_process_dead()` only removes live-mode gauge files) plus an info-level rebalance log.

## Risks / trade-offs

- Approximate enforcement: skewed traffic can exhaust one replica's share while another is idle. Accepted — the previous behavior was N x cap overshoot; under-utilization within the cap is the safer failure mode, and soft-affinity work already reroutes to other accounts.
- Aggregate may exceed the cap when `cap < R` (D2) and during the scale-down window a *departed* replica's share is simply unused (under-admission, not overshoot).
- Between process start and the first successful refresh the replica assumes it is alone (full caps); the window is one registration round-trip.
- Sibling uvicorn workers inside one pod still multiply caps by the worker count (pre-existing; out of scope).

## Completed in this attempt

The first (interrupted) attempt left `cap_partitioning.py`, settings, metrics, `main.py` wiring, and the `load_balancer.py` integration complete and consistent; all were kept unchanged after review (one `ruff format` fixup). This attempt added the OpenSpec artifacts, the unit and multi-replica integration tests, and validation.

## Test plan

- Unit (`tests/unit/test_cap_partitioning.py`): partition math (even split, remainder by rank, floor-at-1, unlimited, single replica); holder hysteresis with an injected clock (scale-up immediate, scale-down deferred/expired, flap recovery, window restart on any pending-partition change including a rank change at the same count, mixed count-up/rank-down churn deferred, self-counting); `refresh_cap_partition` retaining the partition on a failed read.
- Unit (`tests/unit/test_load_balancer_concurrency.py`): `effective_account_concurrency_caps` returns partitioned shares with configured limits and replica count attached; `replica` scope opt-out; partitioned cap error message contract; regression — two `LoadBalancer` instances simulating two replicas admit at most the configured cluster-wide stream cap in aggregate (before this change each replica admitted the full cap).
- Integration (`tests/integration/test_multi_replica.py`): two `RingMembershipService` instances over one shared SQLite database register two replicas; each replica derives its partition from `list_active` through the product refresh path and the aggregate lease admission across two `LoadBalancer` instances equals the configured cap; scale-down after `unregister` is adopted only after the stability window.
