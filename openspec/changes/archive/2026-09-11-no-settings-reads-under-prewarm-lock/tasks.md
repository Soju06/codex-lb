## 1. Lock critical-section audit

- [x] 1.1 Enumerate every call the `prewarm_lock` body can reach that arrives
  at a settings-cache `get()` or a database session, and confirm the two real
  ones (admission gate, reconnect) against the near-miss call sites that are
  outside the lock.

## 2. Regression coverage

- [x] 2.1 Prove a full prewarm body (warm-up built, admission acquired, sent
  to a fake upstream) records exactly one settings read, attributed to the
  prewarm helper before the lock, and none while the lock is held.
- [x] 2.2 Prove the prewarm timeout path hands its pre-lock snapshot to the
  reconnect.
- [x] 2.3 Prove the reconnect completes on a caller-supplied snapshot without
  awaiting the settings cache.
- [x] 2.4 Prove an unreadable settings row falls back to the last loaded one,
  and with no row at all skips the prewarm instead of failing the request.

## 3. Implementation

- [x] 3.1 Resolve the dashboard snapshot before `prewarm_lock` and thread it
  into the admission gate and the reconnect.
- [x] 3.2 Keep both new parameters optional so the unchanged callers keep
  reading the cache exactly as before.
- [x] 3.3 Apply the request entry point's snapshot-failure fallback to the
  pre-lock read so the move cannot add a failure mode.

## 4. Verification

- [x] 4.1 Run the HTTP-bridge and proxy-utils unit suites plus the bridge and
  settings-API integration suites.
- [x] 4.2 Run Ruff, Ty, the full unit suite, and strict OpenSpec validation.
