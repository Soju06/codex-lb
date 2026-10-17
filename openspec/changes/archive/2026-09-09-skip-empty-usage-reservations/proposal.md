## Why

Every keyed request pays the reservation ledger even when the key has no
applicable limits: admission INSERTs an empty `api_key_usage_reservations`
row (zero items) and runs a full-durability commit, and stream end runs the
whole settlement transaction just to flip that empty row to `finalized`.
For unlimited keys this is pure hot-path CPU and write amplification with
no enforcement value — there is nothing to reserve and nothing to settle.

## What Changes

- API-key admission returns no reservation when no configured limit applies
  to the request (key has no limits, or none match the request model). The
  reservation INSERT and its full-durability commit are skipped entirely.
- Downstream reservation consumers (stream/compact settlement, release
  paths, heartbeat touch, quota-planner warmup finalize) already no-op on a
  missing reservation; the quota-planner warmup executor is adapted to
  tolerate admission returning no reservation.
- Because settlement is also the production writer of the key's last-used
  touch, the limit-free admission path records the write-behind coalescer
  touch itself, so dashboard-visible `last_used_at` keeps advancing for
  limit-free keys (per admitted request, at admission time instead of
  stream end).
- Keys with at least one applicable limit are unaffected: reservation
  creation, full commit durability (#1665), exactly-once settlement, and
  stale-reservation reclamation are unchanged for them.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Add a reservation-ledger requirement that limit-free
  admissions skip reservation creation and that downstream settlement,
  release, and heartbeat paths no-op without a reservation.

## Impact

- `app/modules/api_keys/service.py`: `enforce_limits_for_request` (and the
  single-attempt worker) return `None` when no reservation items exist.
- `app/modules/quota_planner/warmup.py`: warmup executor handles a `None`
  reservation (probes without finalizing).
- Per-request effect for unlimited keys: one INSERT + one synchronous
  full-durability commit removed from admission, and the entire stream-end
  settlement transaction removed. No API, setting, dependency, migration,
  or dashboard change. Operator-visible effect: keys without applicable
  limits no longer produce reservation rows, so stale-reservation
  reclamation counts no longer include them.
