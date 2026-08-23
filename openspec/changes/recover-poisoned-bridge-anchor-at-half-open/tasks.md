# Tasks: recover-poisoned-bridge-anchor-at-half-open

## 1. Implementation

- [x] 1.1 Quarantine the bridge key (reason `retry_circuit_poisoned_anchor`)
      when the retry circuit opens on an eventless poison-class failure, so
      the existing quarantine path plans the next full-resend request
      unanchored; leave `clean_close` alone
- [x] 1.2 Report the timer actually refusing a suppressed submission
      (`hard_key_half_open` vs `hard_key_cooldown`) in both the 503
      `retry_after_seconds` and the circuit event detail, and stop claiming
      "cooling down" when the cooldown has expired

## 2. Regression coverage

- [x] 2.1 Two `stream_incomplete` failures quarantine the key with the
      poisoned-anchor reason; two `clean_close` failures do not
- [x] 2.2 Product path: after the circuit opens on eventless failures, the
      next full-resend request through `_stream_via_http_bridge` is prepared
      with no `previous_response_id`
- [x] 2.3 Block reason reports the half-open lease once the cooldown has
      expired (where the legacy cooldown view reports ~0) and the cooldown
      while cooling

## 3. Verification

- [x] 3.1 Run the HTTP bridge unit suite, ruff, ty, the proxy architecture
      check, and strict OpenSpec validation
- [x] 3.2 Reproduce the observed wedge state (two eventless
      `stream_incomplete` failures) against the installed build and confirm
      the key is quarantined, the next full-resend request is planned
      unanchored, and the suppressed follow-up reports a truthful
      retry-after
