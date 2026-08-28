## Why

When HTTP bridge terminal-event bookkeeping pops a request from the session's
pending deque, that continuation becomes the only owner of the request's
API-key reservation settlement. If the continuation raises or is cancelled
before finalization, no cleanup path reaches the request any more: the reader
failure path and the downstream detach only settle requests still in pending
ownership. The orphaned reservation heartbeat then refreshes the reservation's
`updated_at` every heartbeat interval, which permanently exempts it from the
stale-reservation janitor. Quota headroom is held for the process lifetime and
busy keys return spurious 429s (issue #1594).

## What Changes

- Record a settlement claim when terminal-event processing removes requests
  from pending ownership, and settle claimed-but-unfinalized requests
  (heartbeat cancelled, reservation released, downstream waiter unblocked)
  under a shielded scope when the bookkeeping continuation raises or is
  cancelled. This covers both the single terminal path and the grouped
  previous-response error path.
- Allow request detachment to reclaim settlement for a claim that was
  abandoned (its abort settlement also failed) instead of keying solely on
  pending-deque membership.
- Add a hard age ceiling to stale usage-reservation reclamation so a leaked
  heartbeat cannot exempt a reservation from the janitor forever.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: aborted HTTP bridge terminal bookkeeping settles the
  popped request's API-key reservation exactly once.
- `api-keys`: stale usage-reservation reclamation enforces a hard reservation
  age ceiling that heartbeat refreshes cannot extend.

## Impact

- Code: HTTP bridge terminal-event processing, request detachment, stale
  usage-reservation reclamation, and its scheduler.
- Tests: HTTP bridge abort/cancellation regressions and reservation janitor
  coverage.
- Success settlement, accounting, and public response shapes are unchanged.
- No configuration or database schema changes.
