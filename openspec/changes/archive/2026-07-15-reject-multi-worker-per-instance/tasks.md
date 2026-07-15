# Tasks

## 1. Settings tripwire
- [x] 1.1 Keep `workers_per_instance` (`CODEX_LB_WORKERS_PER_INSTANCE`, default 1, `ge=1`) on `Settings` as an explicit operator declaration; document that only 1 is supported.
- [x] 1.2 Add `_validate_workers_per_instance` (`model_validator(mode="after")`) that raises a clear `ValidationError` at startup when `workers_per_instance > 1`, naming `CODEX_LB_WORKERS_PER_INSTANCE` and directing operators to run one worker per pod/container and scale via replicas. `workers_per_instance == 1` (default or explicit) requires no operator action.
- [x] 1.3 Remove `worker_index` / `CODEX_LB_WORKER_INDEX` entirely (no per-worker index anywhere).

## 2. Revert intra-pod worker partitioning
- [x] 2.1 Restore `app/modules/proxy/cap_partitioning.py` to `main` (ring-only `partition_cap(cap, R, rank)`; remove `partition_cap_for_worker`, `partition_stream_reserve`, `worker_count`/`worker_index` on `CapPartition`, `resolve_worker_partition` worker logic).
- [x] 2.2 Restore `app/modules/proxy/load_balancer.py` to `main` (remove `rank`/`worker_index`/`worker_count` on `AccountConcurrencyCaps`, `_effective_stream_cap` worker logic, the per-worker reserve split, worker-spread overload phrasing).
- [x] 2.3 Confirm the ring-based per-replica partitioning path (`bridge_ring_members` → `partition_cap` over `R`, `refresh_cap_partition` wiring in `app/main.py`) is untouched and unchanged from `main`.

## 3. Tests
- [x] 3.1 Restore `tests/unit/test_cap_partitioning.py` and `tests/unit/test_load_balancer_concurrency.py` to `main` (remove worker-partition unit tests: `partition_cap_for_worker`, reserve split, over-subscribed, worker_index matrix). Ring-only partition tests preserved.
- [x] 3.2 `tests/unit/test_settings_multi_replica.py`: `workers_per_instance > 1` raises the fail-fast `ValidationError` (message names the env var and the scale-via-replicas guidance); `workers_per_instance == 1` (default and explicit) is accepted and unchanged.

## 4. Spec + validation
- [x] 4.1 Reframe the change delta: ADD the "multiple worker processes per instance are rejected" requirement (per-replica partitioning unchanged; `W>1` fails fast; scale via replicas); MODIFY "Account-local Responses work is capped before upstream creation" to state the one-worker-per-instance / replica-scaling contract. Remove the obsolete `R*W`, worker_index, reserve-per-worker, and over-subscribed requirements/scenarios. Rationale in `context.md`.
- [x] 4.2 `openspec validate reject-multi-worker-per-instance --strict` and `openspec validate --specs` green.

## 5. CI gates
- [x] 5.1 `uv run ruff check`, `ruff format --check`, `scripts/check_proxy_architecture.py`, `uv run ty check`, target test files green.
