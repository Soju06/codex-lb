# Design

## Problem

`share-account-concurrency-caps` made each configured per-account cap a
cluster-wide target by dividing it across the `R` distinct bridge-ring instance
ids (`partition_cap(cap, R, rank)`), enforced against per-process in-memory lease
counters in `LoadBalancer`. That is correct only when one process runs per
instance id. When an instance runs `W` uvicorn/gunicorn worker processes behind
one bridge instance id:

- All `W` workers register/heartbeat the same instance id, so they occupy a
  single `rank` among `R` members — the ring cannot tell them apart.
- Each worker is a separate OS process with its own `LoadBalancer` in-memory
  lease counters, so each independently admits up to `partition_cap(cap, R,
  rank)`, and the effective cap is multiplied by `W`.

## Why intra-pod worker partitioning was rejected

Making the cap correct under `W > 1` would require each worker to enforce a
distinct slot of an `R*W` partition, which needs a reliable, distinct per-worker
index in `[0, W)`. There is no portable way to obtain one:

- Neither uvicorn `--workers` nor gunicorn exposes a stable per-worker index in
  the child environment.
- A standard multi-worker launch inherits the SAME environment into every child,
  so an operator-declared `CODEX_LB_WORKER_INDEX` lands identically in all
  workers — every worker resolves to slot 0 and enforces slot 0's share
  independently, which over-admits while the other slots go uncovered.
- Auto-deriving the index from PID/hostname hashing does not yield the contiguous
  distinct set `{0..W-1}` a pod needs, so shares collide and the aggregate drifts.
- Shared DB/atomic worker-slot claims add per-request DB round trips on the
  admission hot path — the exact cost the replica change avoided.

Because none of these is reliable, per-worker cap partitioning is not shipped.

## Mechanism

The supported deployment model is **one worker process per pod/container, scaled
horizontally via replicas**. The bridge ring already partitions per-account caps
per replica correctly (`partition_cap(cap, R, rank)`), so this needs no code
change to the partitioning path — it is exactly `main`'s behavior.

The only addition is a fail-fast tripwire. `workers_per_instance`
(`CODEX_LB_WORKERS_PER_INSTANCE`, default 1) is an explicit operator declaration.
A `model_validator(mode="after")` on `Settings` rejects a declared value `> 1` at
startup with a clear error naming the variable and directing the operator to run
one worker per pod/container and scale via replicas. The default `1` is a no-op:
zero operator action, behavior identical to a deployment that never sets it.

The tripwire deliberately catches only the explicit declaration — the worker
count is not portably auto-detectable, so a misconfigured multi-worker launch
that never sets the variable is covered by documentation (one worker per pod),
not by runtime detection. That is the accepted trade-off: a loud, explicit
refusal beats a silent, unreliable partition.

## Rejected alternatives

- **Register each worker as a distinct ring member id**: rejected — the bridge
  instance id is deliberately pod-level for session routing/ownership; per-worker
  ids would fragment bridge affinity and durable-session ownership.
- **`R*W` flattened partition with a per-worker index**: rejected — no portable
  distinct per-worker index (see above); the inherited-environment failure mode
  makes it actively unsafe.
- **Shared DB/atomic worker-slot claims**: rejected — per-request DB I/O on the
  admission hot path.

## SQLite vs PostgreSQL

No schema or query change: the tripwire is process-local settings validation and
the partitioning path is unchanged from `main`. There is no migration and no
dialect-specific behavior on either backend.

## Testing

- Settings validation (`tests/unit/test_settings_multi_replica.py`):
  `workers_per_instance > 1` raises the fail-fast `ValidationError` naming the
  env var and the scale-via-replicas guidance; `workers_per_instance == 1`
  (default and explicit) is accepted and unchanged.
- The existing ring-only partition tests (`tests/unit/test_cap_partitioning.py`,
  `tests/unit/test_load_balancer_concurrency.py`, `tests/integration/
  test_multi_replica.py`) continue to pass unchanged, confirming the per-replica
  partitioning path is untouched.
