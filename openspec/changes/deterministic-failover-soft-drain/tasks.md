# Tasks

## Wave 1 — Foundation (parallel)

- [x] T1: Add `ClassifiedFailure`, `FailureClass`, `FailurePhase` types to `app/core/balancer/types.py`
- [x] T2: Implement `classify_upstream_failure()` in `app/modules/proxy/helpers.py`
- [x] T3: Implement `failover_decision()` pure function in `app/core/balancer/logic.py`
- [x] T4: Add `health_tier: int = 0` field to `AccountState` in `app/core/balancer/logic.py`
- [x] T5: Add `health_tier`, `drain_entered_at`, `probe_success_streak` fields to `RuntimeState` in `app/modules/proxy/load_balancer.py`
- [x] T6: Implement `evaluate_health_tier()` pure function in `app/core/balancer/logic.py` with threshold constants
- [x] T7: Add unit tests for `classify_upstream_failure` (all failure_class mappings)
- [x] T8: Add unit tests for `failover_decision` (all downstream_visible × failure_class × candidates_remaining combinations)
- [x] T9: Add unit tests for `evaluate_health_tier` (tier transitions: HEALTHY→DRAINING→PROBING→HEALTHY, error spike, usage thresholds)

## Wave 2 — Integration (depends on Wave 1)

- [x] T10: Modify `select_account()` in `logic.py` to partition candidates by `health_tier` (`healthy or probing or draining or available`)
- [x] T11: Wire `evaluate_health_tier()` into `_state_from_account()` / `_build_states()` in `load_balancer.py`
- [x] T12: Update `record_success()` to increment `probe_success_streak` when `health_tier == PROBING`
- [x] T13: Update `record_error()` / `record_errors()` to reset `probe_success_streak` when `health_tier == PROBING`
- [x] T14: Modify `_handle_stream_error()` in `service.py` to use `classify_upstream_failure()` and return `ClassifiedFailure`
- [x] T15: Add unit tests for `select_account` with mixed health tiers (prefers HEALTHY, falls back to DRAINING)
- [x] T16: Add unit tests for probe streak tracking in `record_success` / `record_error`

## Wave 3 — Transport failover (depends on Wave 2, parallel within)

- [x] T17: Modify `_stream_with_retry` in `service.py`: catch connect-phase `ProxyResponseError(429/quota)` → `classify_upstream_failure` → `failover_decision` → break to next account if `failover_next`
- [x] T18: Modify `compact_responses` in `service.py`: catch 429/quota `ProxyResponseError` → `classify_upstream_failure` → `failover_decision` → continue to next account if `failover_next`
- [x] T19: Add WebSocket handshake-phase failover: on upstream connect 429/quota before first downstream frame, select next account and retry
- [x] T20: Adjust `_select_with_stickiness()` in `load_balancer.py`: skip new sticky creation for DRAINING accounts (codex_session exempt)
- [x] T21: Add integration tests: streaming connect-phase 429 → transparent failover to account B
- [x] T22: Add integration tests: compact quota_exceeded → transparent failover to account B
- [x] T23: Add integration tests: mid-stream error → surface (no failover)
- [x] T24: Add integration tests: all accounts DRAINING → still selects best DRAINING (no hard block)

## Wave 4 — Feature flags, observability, cleanup (depends on Wave 3)

- [x] T25: Add `soft_drain_enabled` and `deterministic_failover_enabled` to `Settings`
- [x] T26: Add `drain_primary_threshold_pct`, `drain_secondary_threshold_pct`, `probe_quiet_seconds`, `probe_success_streak_required` to `Settings`
- [x] T27: Guard soft-drain logic behind `soft_drain_enabled` flag; guard failover logic behind `deterministic_failover_enabled` flag
- [x] T28: Add structured failover decision logs (request_id, transport, account_id, attempt, failure_class, action)
- [ ] T29: Add Prometheus counters (`codex_lb_failover_total`, `codex_lb_drain_transitions_total`, `codex_lb_client_exposed_errors_total`) gated by `metrics_enabled`
- [x] T30: Verify full existing test suite passes without regression (940 passed, 3 skipped)
