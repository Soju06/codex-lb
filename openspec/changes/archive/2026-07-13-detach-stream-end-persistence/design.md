## Context

`_write_request_log` created a task and `asyncio.shield`-awaited it, detaching into `_request_log_tasks` only when the caller was cancelled; `_settle_stream_api_key_usage` did the same into `_background_cleanup_tasks`, with a done-callback that already schedules `_release_unsettled_stream_api_key_usage` on failure/cancellation and a caller finally-net keyed on `usage_settlement_transferred`. Neither task set was drained at shutdown.

## Goals / Non-Goals

**Goals:** stream close never blocks on persistence; identical settlement safety (exactly-once finalize-or-release); no lost writes on graceful shutdown; deterministic tests.

**Non-Goals:** compact/transcribe settlement (different inline shape, not stream-close latency), websocket reservation heartbeats, the reservation expiry reaper, changing what is persisted.

## Decisions

- **Reuse the cancellation-path machinery unconditionally** rather than inventing a queue: the transfer path (tracking + failure fallback + transferred flag) was already the hardened code path; the shield-await was the only thing making the response wait. Detaching = always taking the transfer path.
- **Admission safety**: reservations count toward limits from reserve until finalize/release, so a settlement that lags by milliseconds keeps the reservation counted — deferred settlement can only briefly over-restrict, satisfying the settle-before-anything-user-visible concern without ordering changes (error-health writes were already issued before settlement in the retry flow).
- **Shutdown drain** (`drain_persistence_tasks`) runs in the existing lifespan teardown before bridge/database teardown, bounded by `shutdown_drain_timeout_seconds`, logging any task that fails to drain.
- **Test determinism via a response hook**, not per-test edits: the suite's `async_client` drains persistence after every response, preserving the read-after-response idiom in ~100 existing tests; unit tests that construct `ProxyService` directly drain explicitly; two dedicated hook-free tests pin the detach contract (close-before-persist, drain-timeout reporting).

## Risks / Trade-offs

- [Dashboard sees a request's log a few ms late] → observational data; the images model rewrite already retries while the row is missing.
- [Process crash (non-graceful) loses in-flight log writes/settlements] → unchanged from before for cancellation-transferred tasks; settlements additionally have the reservation expiry reaper as the backstop.
- [Unbounded task accumulation under failure storms] → tasks complete (success or logged failure) per request; the sets are bounded by in-flight request counts.

## Migration Plan

Code-only; rollback = revert.

## Open Questions

None.
