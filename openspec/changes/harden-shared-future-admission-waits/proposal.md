# Harden shared-future admission waits and surface event-loop lag

## Why

On 2026-08-20 a production instance livelocked: the event loop spent ~98% of
its CPU inside `asyncio.Future.remove_done_callback` and kept grinding at full
CPU with zero client sessions attached. Admission waiters were piling onto
shared registry futures via `asyncio.wait_for(asyncio.shield(...))`, which
attaches per-waiter callbacks to the shared future and removes them with O(n)
scans; on Python 3.14 `shield` additionally leaks one callback per attempt
onto a still-pending future, so mass timeouts and client-disconnect storms
degrade to O(n²) and starve the loop. The outage surfaced only as global
slowness and health-check flapping — no signal said "the event loop itself is
starved", which stretched diagnosis by hours.

## What Changes

- Replace `wait_for(shield(shared))` on shared, many-waiter futures (http-bridge
  inflight/capacity registries, token-refresh singleflight) with a fan-out
  helper that keeps exactly one callback on the shared future and gives each
  waiter a private O(1)-detach proxy future. Wait semantics (result/exception/
  cancellation propagation, timeout contract, waiter cancellation isolation)
  are unchanged.
- Add an event-loop lag watchdog: a once-per-second sampler that exports
  `codex_lb_event_loop_lag_seconds` / `codex_lb_event_loop_lag_warnings_total`
  and emits a rate-limited warning log when scheduling lag crosses a threshold.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: admission waits on shared futures must not attach
  per-waiter callbacks to the shared object.
- `proxy-runtime-observability`: event-loop scheduling lag is an explicit
  operator signal (metrics + rate-limited warning log).

## Impact

- `app/core/utils/shared_future.py` (new helper), `app/modules/proxy/_service/http_bridge/mixin.py`,
  `app/modules/accounts/auth_manager.py` (call-site swaps; no behavior change).
- `app/core/resilience/loop_lag_monitor.py` (new watchdog),
  `app/core/metrics/prometheus.py`, `app/main.py`,
  `app/core/config/settings.py` (`event_loop_lag_warn_threshold_seconds`,
  default 0.5s, `0` disables; zero-config — no operator action needed).
