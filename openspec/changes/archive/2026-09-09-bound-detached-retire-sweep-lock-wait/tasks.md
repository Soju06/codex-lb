## 1. Bound the Sweep

- [x] 1.1 Add `lock_wait_timeout_seconds` to `_retire_http_bridge_after_drain_if_ready` (bounded acquire, warning on timeout, unchanged locked body, unbounded when `None`).
- [x] 1.2 Pass a fixed 5 second bound from the per-request detached-session sweep.

## 2. Regression Coverage

- [x] 2.1 Sweep against a permanently held detached lock returns within the bound, closes nothing, and leaves the lock owned by its holder.
- [x] 2.2 Sweep against a free lock still retires; a lifecycle owner without the bound still waits.

## 3. Validation

- [x] 3.1 Focused unit tests, lint, format, type, and architecture checks.
- [x] 3.2 Strict change-local OpenSpec validation.
