## 1. Shared-future waiter fan-out

- [x] 1.1 Add `wait_on_shared_future` (`app/core/utils/shared_future.py`): one fan-out callback on the shared future, per-waiter proxy futures with O(1) attach/detach, `wait_for(shield())`-equivalent semantics.
- [x] 1.2 Swap the http-bridge admission wait sites (inflight session future, capacity wait future) in `app/modules/proxy/_service/http_bridge/mixin.py` to the helper.
- [x] 1.3 Swap the token-refresh singleflight wait in `app/modules/accounts/auth_manager.py` to the helper.

## 2. Event-loop lag watchdog

- [x] 2.1 Add `app/core/resilience/loop_lag_monitor.py`: 1s sleep-drift sampler, gauge + counter export, rate-limited warning log.
- [x] 2.2 Register `codex_lb_event_loop_lag_seconds` and `codex_lb_event_loop_lag_warnings_total` in `app/core/metrics/prometheus.py` (both branches + `__all__`).
- [x] 2.3 Wire the monitor task into the app lifespan (`app/main.py`) behind `event_loop_lag_warn_threshold_seconds` (default 0.5, `0` disables), cancelled on shutdown.

## 3. Verification

- [x] 3.1 Helper semantics tests (`tests/unit/test_shared_future_waiters.py`): result/exception/cancellation propagation, timeout leaves shared pending, mass-timeout keeps callback count at 1, waiter cancellation isolation, singleflight task survival.
- [x] 3.2 Bridge-surface regression test (`tests/unit/test_proxy_http_bridge.py::test_admission_waiters_do_not_accumulate_callbacks_on_shared_inflight_future`): 50 admission waiters on one inflight future keep exactly one shared callback through a cancellation storm and mass timeout; verified to fail against the old shield pattern.
- [x] 3.3 Watchdog tests (`tests/unit/test_loop_lag_monitor.py`): starved loop warns + increments counter, healthy loop stays quiet, warning log rate-limited.
- [x] 3.4 Run affected unit suites, ruff, and strict OpenSpec validation.
