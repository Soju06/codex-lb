# Share account concurrency caps across replicas

## Why

Per-account concurrency caps (`proxy_account_response_create_limit` default 4, `proxy_account_stream_limit` default 8) are enforced against in-process lease counters in `LoadBalancer`. Every replica therefore admits the full configured cap, so an N-replica deployment multiplies effective per-account concurrency by N — three replicas turn a stream cap of 8 into 24 concurrent streams on one ChatGPT account. This defeats the operator's per-account protection exactly in the HA deployments that need it, driving upstream 429s and account risk. The caps must become cluster-wide targets without adding per-request database round trips or cross-replica lock traffic on the hot admission path.

## What Changes

- New `app/modules/proxy/cap_partitioning.py`: each replica derives its share of every configured cap deterministically from the sorted active bridge-ring member list — `floor(cap / R)` plus one extra slot when its rank is below `cap mod R`, floored at one slot so an account never becomes unroutable on a replica; `cap <= 0` stays unlimited. No shared mutable state and no per-request DB I/O.
- Direction-aware hysteresis keyed on the share direction: membership changes that shrink or keep this replica's share (count increases, or same-count churn toward a later rank) are adopted on the next refresh; changes that would grow the share (count decreases, or same-count churn toward an earlier rank, e.g. a rolling replacement swapping a draining member for a new instance id) are adopted only after the growing observation has been held continuously for `proxy_account_cap_partition_scale_down_seconds` (default 60, minimum 30), so neither a missed heartbeat nor rolling churn can transiently inflate a survivor's share.
- Lifespan wiring in `app/main.py`: the partition refreshes from `RingMembershipService.list_active` after ring registration and after every 10s ring heartbeat. A failed membership read retains the last-known partition instead of falling open to the full caps. The observing replica always counts itself.
- `effective_account_concurrency_caps` partitions the dashboard/env-configured caps; every existing admission call site (selection filter, lease acquisition, opportunistic admission, cap waits) inherits the partitioned values. `proxy_account_caps_scope=replica` restores legacy per-replica semantics.
- Account-cap local overload messages state the replica's share, the configured cluster-wide limit, and the replica count when more than one replica is active; the stable reasons `account_response_create_cap` / `account_stream_cap` are unchanged.
- New gauge `codex_lb_cap_partition_replicas` and an info-level rebalance log (old count, new count, rank).
- No DB migration: partitioning only reads the existing `bridge_ring_members` table.

## Non-goals

- Exact global enforcement through shared DB counters or a distributed lease table (rejected: per-request DB round trips on the admission hot path).
- Per-worker sub-partitioning inside one pod: sibling uvicorn workers share one instance id and keep separate in-process counters, a pre-existing limitation orthogonal to replica fan-out.
- Load-following dynamic rebalancing between replicas (static deterministic shares only).
