## 1. Runtime state

- [x] 1.1 Add `RuntimeState.soft_overload_rejections`
- [x] 1.2 Deep-copy the new list in the opportunistic-admission snapshot

## 2. Accounting

- [x] 2.1 Add `UPSTREAM_SOFT_OVERLOAD_CODES` and `SOFT_OVERLOAD_TRIP_WEIGHT`
- [x] 2.2 `record_overload_rejection_locked(..., soft=False)` weighted trip
- [x] 2.3 `record_upstream_overload(..., soft=False)` passthrough

## 3. Funnel

- [x] 3.1 Record a soft observation for a `server_error` stream terminal
- [x] 3.2 Leave the HTTP 429 burst-cooldown branch owning `http_status == 429`
- [x] 3.3 Add the soft-gate-only `upstream_http_status` keyword so a caller can
      report a known upstream status to this window without taking any of the
      positional `http_status` side effects
- [x] 3.4 Report the known status from the terminal-renderer and bridge call
      sites that previously dropped it

## 4. Spec + tests

- [x] 4.1 MODIFIED `account-routing` overload requirement + scenarios
- [x] 4.2 Unit tests: soft-only trip, mixed trip, window pruning, funnel wiring,
      429 regression guard
- [x] 4.3 Unit tests per fixed call-site shape: terminal-renderer HTTP 5xx,
      bridge `error` frame carrying `status`, evidence-keyword-never-arms-burst,
      and the status-less over-correction guard
- [x] 4.4 `pytest tests/unit/test_overload_backoff.py` green
