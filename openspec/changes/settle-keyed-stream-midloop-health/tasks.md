## 1. Implementation

- [x] 1.1 Defer keyed mid-loop stream health while an API-key reservation is held.
- [x] 1.2 Flush deferred health only after confirmed settlement (or confirmed
  fail-safe release when ordered settle never ran).
- [x] 1.3 Record `settled` before awaiting deferred health flush so cancel
  during flush cannot skip retained penalties.
- [x] 1.4 Isolate deferred health entries so one failed or cancelled write does
  not drop later entries.
- [x] 1.5 Schedule cancel-safe leftover flush when cleanup runs under an already
  cancelling task.

## 2. Regression coverage

- [x] 2.1 Assert keyed refresh/connect failover settles before health.
- [x] 2.2 Assert keyed transient exhaustion settles before health.
- [x] 2.3 Assert cancel after a queued mid-loop penalty still flushes health.
- [x] 2.4 Assert cancel during deferred health flush still applies the penalty.
- [x] 2.5 Assert a later deferred health write still runs after an earlier
  write fails.
- [x] 2.6 Assert the streaming `/v1/responses` product entry preserves
  settle-before-health for keyed mid-loop failover.

## 3. Validation

- [x] 3.1 Run the keyed mid-loop settle/flush regressions.
- [x] 3.2 Run strict OpenSpec validation for this change.
