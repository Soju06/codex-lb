## Why

Production (soju07, 2026-09-07) ran for four days with five HTTP-bridge `pending_lock` instances in an unrecoverable state: no owner task, yet a non-empty waiter queue whose futures were never resolved. One of them sat under the per-request fail-safe handoff sweep (`_release_http_bridge_unanchored_handoffs_for_request` → `_retire_http_bridge_after_drain_if_ready`), so roughly 100 live request tasks were queued behind a detached session's lock with no way out. The other four pinned bridge cleanup tasks, which in turn kept cancelled request chains alive (see `fix-probe-task-cancel-cascade-spin`).

The root cause is a lost wake-up in anyio 4.13's asyncio `Lock`/`Semaphore`: `release()` skips waiters whose futures are already cancelled and sets the owner to `None`, but those cancelled entries stay queued until their tasks run their `except` branch. An acquirer arriving in that window sees "no owner, non-empty queue", takes the slow path, and is never handed ownership once the cancelled entries remove themselves. anyio 4.14.0 fixes exactly this ("Fixed asyncio Lock and Semaphore deadlocks caused by cancelled waiters left queued during release"). The repository pins anyio only transitively (4.13.0 in `uv.lock`).

## What Changes

- Declare `anyio>=4.14.0` as a direct dependency and lock anyio 4.15.1 so the deadlock-free release path ships in the next release.
- Add a deterministic unit regression that drives the exact release/cancel/acquire interleaving against `anyio.Lock`, `anyio.Semaphore`, and the stdlib `asyncio.Lock` reference, so a future dependency downgrade or a re-implementation of the primitive cannot silently reintroduce the deadlock.
- No public API, configuration, persistence schema, or wire-format changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-architecture`: Require the asyncio synchronization primitives used by the proxy to remain acquirable after a release that coincides with a cancelled waiter.

## Impact

Affected code is limited to `pyproject.toml`, `uv.lock`, one unit test module, and the `proxy-architecture` spec. Runtime behaviour changes only in the fixed dependency: `anyio.Lock`/`anyio.Semaphore` no longer wedge when a cancelled waiter is left queued during release. Every `anyio.Lock()` in the application (23 instances, including the HTTP bridge `pending_lock`, the bridge registry lock, and the websocket mixin locks) benefits without code changes.
