## Why

`_release_http_bridge_unanchored_handoffs_for_request` is a fail-safe sweep that runs on every HTTP-bridge request. For each detached session it calls `_retire_http_bridge_after_drain_if_ready`, which acquires that session's `pending_lock` with no bound. On 2026-09-07 one detached `thread_header` generation's lock was left permanently unowned by an anyio 4.13 lost-wakeup, and roughly 100 live request tasks queued behind it inside this sweep. The lock bug is fixed by the dependency floor (`fix-anyio-lock-cancelled-waiter-deadlock`), but a hot path shared by every request must not be able to park the fleet behind a single detached generation, whatever the reason its lock stays busy.

## What Changes

- `_retire_http_bridge_after_drain_if_ready` accepts an optional `lock_wait_timeout_seconds`. When set and the bound elapses, it logs a warning, leaves the session tracked, and returns `False` without touching session state; when `None` (lifecycle owners) the wait stays unbounded.
- The per-request fail-safe sweep passes a fixed 5 second bound for detached sessions. A skipped session is reconsidered by the next request's sweep and by its own drain/close paths.
- Regressions: a sweep against a detached session whose lock is held forever returns within the bound without breaking the lock or closing the session; a free lock still retires; lifecycle owners without the bound still wait for the lock.
- No public API, configuration, persistence schema, or wire-format changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Require the per-request detached-session retire sweep to bound its wait on a session's `pending_lock`.

## Impact

Affected code: `helpers.py::_release_http_bridge_unanchored_handoffs_for_request`, `request_submit.py::_retire_http_bridge_after_drain_if_ready`, the bridge protocol stub, and unit regressions. Retirement semantics are unchanged whenever the lock is obtained; only the failure mode of an unobtainable lock changes from "every request waits forever" to "this pass skips, with a warning".
