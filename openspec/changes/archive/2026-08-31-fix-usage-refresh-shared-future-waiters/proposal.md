## Why

The usage-refresh singleflight was omitted when shared, many-waiter futures
were hardened after the 2026-08-20 event-loop livelock. After roughly 91 hours
of production uptime, cancelled usage-refresh waiters again drove the event
loop into the same `asyncio.shield` callback-removal failure mode, so this
remaining shared wait site must use the established fan-out helper.

## What Changes

- Route both usage-refresh singleflight wait paths through
  `wait_on_shared_future`, preserving result, cancellation, exception, and
  `join_existing=False` sequencing semantics.
- Keep the shared refresh factory task running when an individual waiter is
  cancelled or times out, with one bounded fan-out callback on that task.
- Add usage-refresh surface regression coverage for concurrent joins,
  cancellation isolation, callback fan-out, and successor sequencing.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: extend the established shared-future admission
  contract to usage-refresh singleflight waiters.

## Impact

- `app/modules/usage/updater.py` and `tests/unit/test_usage_updater.py`.
- No API, schema, configuration, dependency, or deployment changes.
- This is a focused follow-on to
  [`harden-shared-future-admission-waits`](../harden-shared-future-admission-waits/)
  and relies on the existing
  [`proxy-admission-control`](../../specs/proxy-admission-control/) wait
  mechanism and
  [`proxy-runtime-observability`](../../specs/proxy-runtime-observability/)
  event-loop lag signals.
