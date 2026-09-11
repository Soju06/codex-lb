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

## 4. Spec + tests

- [x] 4.1 MODIFIED `account-routing` overload requirement + scenarios
- [x] 4.2 Unit tests: soft-only trip, mixed trip, window pruning, funnel wiring,
      429 regression guard
- [x] 4.3 `pytest tests/unit/test_overload_backoff.py` green
