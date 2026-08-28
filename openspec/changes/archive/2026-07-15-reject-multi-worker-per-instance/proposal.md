# Reject unsupported multi-worker-per-instance for shared per-account caps

## Why

Per-account concurrency caps are cluster-wide targets partitioned **per replica**
via the bridge ring (`share-account-concurrency-caps`): each replica derives its
share of every cap from the sorted active `bridge_ring_members` list. That is
correct only when a single process runs behind each bridge-ring instance id.

An instance that runs several uvicorn/gunicorn worker processes behind ONE bridge
instance id breaks the invariant: sibling workers share one ring rank yet keep
separate in-process lease counters, so each worker independently enforces the
whole replica share and the effective per-account cap is multiplied by the worker
count. Intra-pod worker cap partitioning cannot be made reliable to fix this:
there is no portable per-worker index, and a standard multi-worker launch inherits
the SAME environment (any declared `WORKER_INDEX`) into every child, so the
workers cannot self-partition into distinct slots.

Rather than ship an unworkable per-worker contract, the supported model is **one
worker per pod/container, scaled horizontally via replicas** — the bridge ring
already partitions per-account caps per replica correctly. An operator who
explicitly declares more than one worker per instance must be told fast, at
startup, instead of silently over-admitting against every account.

## What Changes

- Add a single fail-fast tripwire: `workers_per_instance`
  (`CODEX_LB_WORKERS_PER_INSTANCE`, default 1). A value `> 1` raises a clear
  settings `ValidationError` at startup explaining that multi-worker-per-instance
  is unsupported for shared caps and that operators should run one worker per
  pod/container and scale via replicas. Default `1` is a no-op requiring zero
  operator action — behavior is identical to today.
- Per-account cap partitioning stays exactly as it is on `main`: ring-based, per
  replica (`partition_cap(cap, R, rank)`), with no worker dimension.

## Non-goals

- Intra-pod multi-worker cap partitioning (an `R*W` slot split, per-worker index,
  per-worker reserve split): dropped as unworkable — no portable per-worker index
  and inherited environment make it unreliable.
- Auto-detecting the worker count: not portably detectable. The tripwire only
  catches the explicit `CODEX_LB_WORKERS_PER_INSTANCE > 1` declaration;
  documentation covers the one-worker-per-pod deployment guidance. This is the
  accepted trade-off.
