# Tasks

- [x] 1. Create OpenSpec change artifacts (proposal, design, tasks, delta specs for MODIFIED/ADDED `proxy-admission-control` and ADDED `proxy-runtime-observability`) and pass `openspec validate share-account-concurrency-caps --strict`
- [x] 2. Add `app/modules/proxy/cap_partitioning.py`: `partition_cap` (floor/remainder-by-rank, floor-at-1, `cap <= 0` unlimited), `CapPartition`, `CapPartitionHolder` with direction-aware scale-down hysteresis, module-global holder with `get_cap_partition` / `observe_ring_members` / `refresh_cap_partition` / test reset
- [x] 3. Settings: `proxy_account_caps_scope: Literal["partitioned", "replica"] = "partitioned"` and `proxy_account_cap_partition_scale_down_seconds: int = 60 (ge=30)` in `app/core/config/settings.py`
- [x] 4. Wire refresh into the `app/main.py` lifespan ring loop: after successful registration and after every heartbeat tick; failed reads retain the last-known partition
- [x] 5. `app/modules/proxy/load_balancer.py`: extend `AccountConcurrencyCaps` with configured limits + replica count; partition caps in `effective_account_concurrency_caps` (scope + single-replica short-circuit); replica-share account-cap error messages
- [x] 6. Metrics: `codex_lb_cap_partition_replicas` gauge (multiprocess mode `livemax`, so dead workers do not pin a stale higher count) in `app/core/metrics/prometheus.py`; info-level rebalance log
- [x] 7. Unit tests: `tests/unit/test_cap_partitioning.py` (partition math, hysteresis with injected clock, fail-closed refresh) and `tests/unit/test_load_balancer_concurrency.py` (partitioned effective caps, `replica` scope opt-out, error message, two-replica aggregate lease regression)
- [x] 8. Integration tests: `tests/integration/test_multi_replica.py` — two `RingMembershipService` replicas over one SQLite DB drive partition derivation and aggregate cap enforcement across two `LoadBalancer` instances; scale-down hysteresis over the ring
- [x] 9. Run targeted pytest, `ruff check` + `ruff format --check`, and `openspec validate` until green
