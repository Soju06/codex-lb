# Fix defer-cancellation shield leak (2026-08-30 event-loop livelock)

## Why

Production starved its event loop again on 2026-08-30 (~4 days of uptime on
1.24.0): a live-process autopsy found 26 futures carrying 500+ leaked
`asyncio.shield` callbacks — the worst a wedged
`_cleanup_http_bridge_submit_interruption` task with 100,986 — and ~50% of
main-thread GIL samples inside `Future.remove_done_callback` O(n) scans.

The http-bridge copy of `_await_task_deferring_cancellation` was the one
defer-cancellation loop that never received the anyio `CancelScope(shield=True)`
guard its `streaming/retry.py` sibling got in #1645. Under a level-cancelled
Starlette scope (client disconnect), anyio re-delivers cancellation at every
`await`, so the bare `while True: await asyncio.shield(task)` loop busy-spun
at event-loop speed, and Python 3.14's `shield` leaks a done-callback onto the
still-pending inner task for every cancelled wait (~10-20k callbacks/second
measured). Two more unguarded copies (`compact.py` flush loop,
`db/session.py::_shielded_bounded`) and two repeat-shield tick loops
(SSE keepalive injector, timed waits on the shared per-session
`resource_close_task`) share the same failure class.

## What Changes

- Guard the http-bridge `_await_task_deferring_cancellation` with
  `anyio.CancelScope(shield=True)` (stops the level-cancel busy-spin) and wait
  through `wait_on_shared_future` (keeps the owned task's callback list
  bounded under edge cancels), matching and hardening the retry-module copy.
- Apply the same guard+wait conversion to the compact deferred-health flush
  loop and to `db/session.py::_shielded_bounded` (deadline-preserving
  `asyncio.wait` drain).
- Convert the two repeat-shield tick waits — the SSE keepalive injector's
  per-tick wait on the pending chunk task and the timed wait on the shared
  http-bridge `resource_close_task` — to `wait_on_shared_future`.
- Add regression coverage proving callback counts stay bounded under a
  level-cancelled anyio scope, repeated edge cancels, and quiet-upstream
  keepalive ticks, while defer-cancellation semantics are preserved.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: extend the established shared-future wait
  contract to defer-cancellation waits on owned cleanup tasks and repeated
  timed waits (keepalive ticks, bounded teardown drains).

## Impact

- A client-disconnect storm or a slow/wedged cleanup task can no longer grow
  task callback lists without bound or busy-spin the event loop; loop-lag
  under those conditions returns to baseline.
- Cleanup ownership semantics are unchanged: owned tasks are never cancelled
  by their waiters, the caller's cancellation is still deferred until the
  owned task finishes and then re-raised, and owned-task cancellation and
  exceptions propagate exactly as before.
- API response shapes do not change. No new settings.
