# Tasks: recover-poisoned-bridge-anchor-at-half-open

## 1. Implementation

- [x] 1.1 Abandon durable continuity on the half-open transition when the
      last recorded failure is an eventless poison class, before admitting
      the probe; leave the anchor intact for `clean_close`
- [x] 1.2 Bound the half-open lease to one base backoff
- [x] 1.3 Report the timer actually refusing a suppressed submission
      (`hard_key_half_open` vs `hard_key_cooldown`) in both the 503
      `retry_after_seconds` and the circuit event detail, and stop claiming
      "cooling down" when the cooldown has expired

## 2. Regression coverage

- [x] 2.1 Half-open transition with a poison last-detail clears the anchor
      through the fenced durable write with `clear_continuity=True`
- [x] 2.2 Half-open transition with `clean_close` does not touch the anchor
- [x] 2.3 Block reason reports the half-open lease once the cooldown has
      expired (where the legacy cooldown view reports ~0) and the cooldown
      while cooling
- [x] 2.4 Lease bound is no greater than the base backoff

## 3. Verification

- [x] 3.1 Run the HTTP bridge unit suite, ruff, ty, the proxy architecture
      check, and strict OpenSpec validation
- [x] 3.2 Reproduce the observed wedge state (two eventless
      `stream_incomplete` failures, cooldown expired) against the installed
      build and confirm the probe is admitted with the anchor cleared, the
      suppressed follow-up reports a truthful half-open retry-after, and the
      worst-case lockout is one base backoff
