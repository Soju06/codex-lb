# Context

## Why multi-worker-per-instance is refused, not partitioned

Per-account concurrency caps are cluster-wide targets partitioned **per replica**
by the bridge ring: each replica derives its share of every cap from the sorted
active `bridge_ring_members` list (`partition_cap(cap, R, rank)`), enforced
against per-process in-memory lease counters. This is correct only when a single
process runs behind each ring instance id.

Running several worker processes behind one instance id breaks it: sibling
workers share one ring rank but keep separate in-process counters, so each
enforces the whole replica share and the effective cap is multiplied by the
worker count. The obvious fix — an `R*W` slot partition with a distinct
per-worker index — cannot be made reliable:

- **No portable per-worker index.** Neither uvicorn `--workers` nor gunicorn
  exposes a stable index in the child environment.
- **Inherited environment.** A standard multi-worker launch copies the SAME
  environment into every child, so an operator-declared `CODEX_LB_WORKER_INDEX`
  is identical in all workers — every worker resolves to slot 0 and enforces slot
  0's share independently. That is worse than no partitioning: it looks
  configured but silently over-admits (e.g. `W=3`, stream cap 8: each worker
  enforces slot 0's share of 3, aggregate 9 > 8) while slots 1..W-1 go uncovered.
- **Auto-detection isn't portable either**, and shared-DB slot claims would add
  per-request I/O on the admission hot path.

The bridge ring already solves the real need — scaling per-account cap capacity —
by partitioning per replica. So the supported model is **one worker per
pod/container, scaled horizontally via replicas**, and an explicit declaration of
more than one worker is refused rather than mis-served.

## The tripwire and its accepted limitation

`workers_per_instance` (`CODEX_LB_WORKERS_PER_INSTANCE`, default 1) is the only
worker-related setting. A `model_validator(mode="after")` on `Settings` (the same
pattern as the other cross-field validators, e.g.
`_validate_token_refresh_claim_ttl`) rejects a declared value `> 1` at startup
with a clear error naming the env var and directing the operator to run one
worker per pod/container and scale via replicas. The default `1` is a complete
no-op: zero operator action, behavior identical to `main` today.

The tripwire catches only the **explicit declaration**. The worker count is not
portably auto-detectable, so an operator who launches multiple workers without
setting `CODEX_LB_WORKERS_PER_INSTANCE` is not caught at runtime — that case is
covered by deployment documentation (one worker per pod). This is a deliberate,
accepted trade-off: a loud refusal of the declared-unsupported config is worth
more than an unreliable partition, and auto-detection would give false
confidence it cannot back up.

## What was removed from the earlier direction

An earlier revision of this change added intra-pod worker cap partitioning: an
`R*W` flattened `partition_cap_for_worker`, `worker_count`/`worker_index` on
`CapPartition` and `AccountConcurrencyCaps`, a per-worker stream-reserve split,
an ordinary-floor-at-zero rule for over-subscribed shares, and a
`CODEX_LB_WORKER_INDEX` setting with per-worker-index validation. All of that is
removed for the reasons above; the partitioning path is byte-for-byte `main`'s
ring-only behavior, and the branch's only code change versus `main` is the
`workers_per_instance > 1` tripwire.
