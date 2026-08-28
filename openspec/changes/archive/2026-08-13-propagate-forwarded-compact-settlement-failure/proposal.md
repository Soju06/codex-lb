## Why

An HTTP-bridge owner receives the origin's API-key usage reservation with
`owns_reservation` false, so compact settlement on the owner is the reservation's
only persistence path. `_settle_compact_api_key_usage` currently logs and
swallows persistence exceptions, which can make the owner report the original
compact result while the reservation remains held until stale cleanup.

## What Changes

- Propagate compact API-key settlement persistence failures after logging them,
  so an owner-forwarded request cannot report a successfully handled terminal
  when its sole settlement did not persist.
- Attempt a fail-safe release through a fresh repository before surfacing the
  failure, while preserving the origin's existing idempotent reservation cleanup
  and adding no background retry or stale-cleanup mechanism.
- Add a signed forwarded-route failure regression that injects a settlement
  persistence error and verifies both the surfaced error and the reservation's
  final status.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Require owner-forwarded compact settlement failures to
  remain observable and fail closed instead of being swallowed.

## Impact

- Affected code: `app/modules/proxy/_service/api_key_usage.py` and
  `app/modules/proxy/_service/compact.py`.
- Affected tests: `tests/integration/test_proxy_compact.py`.
- No API schema, setting, dependency, migration, quota-cleanup, or WebSocket
  health behavior changes.
