# Tasks

## 1. Admission skip

- [x] 1.1 Return `None` from `ApiKeysService._enforce_limits_for_request_once`
      when `reservation_items` is empty (no applicable limits), skipping the
      reservation INSERT and its commit; widen `enforce_limits_for_request`
      return type to `ApiKeyUsageReservationData | None`.

## 2. Consumer audit / adaptation

- [x] 2.1 Verify settlement (`_settle_stream_api_key_usage`,
      `_settle_compact_api_key_usage`), release paths
      (`_release_reservation*`, `_release_websocket_reservation`),
      heartbeat (`_maybe_touch_api_key_reservation`), bridge forwarding
      (`_reservation_from_headers`), and retry re-reservation guards all
      no-op on a `None` reservation.
- [x] 2.2 Adapt the quota-planner warmup executor to a `None` reservation
      (probe without finalize).
- [x] 2.3 Record the key's last-used coalescer touch at admission on the
      limit-free path (settlement, the production `last_used_at` writer,
      never runs without a reservation).
- [x] 2.4 Roll back the admission read transaction before the limit-free
      early return (long-lived sessions must not idle in transaction
      across upstream round-trips).

## 3. Tests

- [x] 3.1 Unit: key without limits → admission returns `None`, no
      reservation INSERT; the only commit is the read-only transaction close.
- [x] 3.2 Unit: key whose limits do not match the request model → `None`,
      limits untouched.
- [x] 3.2b Unit: limit-free admission records the last-used coalescer touch
      and closes the read transaction via commit — never rollback, which
      would expire shared-session ORM state (quota-planner warmup).
- [x] 3.3 Unit: settlement/release/heartbeat with `reservation=None` no-op.
- [x] 3.4 Integration: quota-planner warmup executes with a limit-free key
      (no finalize call, probe succeeds).
- [x] 3.4b Integration (regression): warmup through the REAL ApiKeysService
      on the shared session — the probe's `account.access_token_encrypted`
      access stays readable after the limit-free early return (no
      MissingGreenlet from expired shared state).
- [x] 3.5 Integration: stale-release reclamation finds no rows after
      limit-free admissions; limited keys keep creating reservations
      (regression).

## 4. Spec

- [x] 4.1 Delta to `openspec/specs/api-keys/spec.md` reservation-ledger
      requirements; validate with `openspec validate --specs`.
