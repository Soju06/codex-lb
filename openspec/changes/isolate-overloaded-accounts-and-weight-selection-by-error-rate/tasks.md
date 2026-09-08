# Tasks

## 1. Overload isolation stage

- [x] 1.1 Add `OverloadIsolationPolicy` (`CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS`, `0` disables) and `OVERLOAD_ISOLATION_TRIP_LEVEL`; `record_overload_rejection_locked` uses the isolation interval at that level and stamps `RuntimeState.overload_isolated_until`; level decay is measured from the later of the last trip and the backoff deadline.
- [x] 1.2 `record_upstream_overload` logs `Account overload isolation engaged` at the isolation trip and keeps the soft-backoff log below it.
- [x] 1.3 `sticky_owner_isolation_reroute_pool`: an isolated soft owner is released only while the pool still holds an overload-free candidate.
- [x] 1.4 Sticky selection releases an isolated `prompt_cache` / `sticky_thread` / `codex_session` owner when the strategy selects from the overload-free pool (rebinding the mapping, probe reservation sees that pool), keeps it otherwise, and skips a backed-off process-session preference for a fresh thread while an overload-free candidate exists.

## 2. Error-rate weighting

- [x] 2.1 Add `error_rate.py` (minute-bucket outcome window, `ErrorRateWeightingPolicy`, `error_rate_weight_multiplier`) and `RuntimeState.outcome_buckets`.
- [x] 2.2 `LoadBalancer.record_errors` / `record_success` record outcomes under the per-account lock; the selection `AccountState` carries `selection_weight_multiplier`.
- [x] 2.3 `capacity_weighted` and `relative_availability` multiply draw weights by the clamped multiplier; top-k membership and deterministic probes are unchanged.

## 3. Verification

- [x] 3.1 Unit tests: isolation trip/interval/disable, decay-from-deadline, isolation log, reroute pool predicate, sticky owner release per kind, soft-backoff-keeps-owner, lone/rate-limited sibling keeps owner, expiry, process-session preference; window pruning, multiplier thresholds/floor/disable, weighted draws honor and clamp the multiplier, balancer-level steering with `error_count` latch reset.
- [x] 3.2 ruff, ty, proxy architecture check (load_balancer.py at the 3021 ratchet), settings reference regenerated and ratchet raised to 135, strict OpenSpec validation.
