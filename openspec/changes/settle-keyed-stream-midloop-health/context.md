## Purpose

Document the settle-before-health invariant for keyed HTTP SSE mid-loop
failover: one API-key reservation spans internal account replacement, and
account-health penalties flush only after settlement is visible to cleanup.

## Rationale

Writing health while a reservation is still open can double-charge usage
accounting and backoff an account the request has not finished using. Recording
settlement only via `settled = await settle_and_flush(...)` is unsafe under
cancellation: settlement may commit inside the await while the assignment never
runs, which skips both unsettled-reservation cleanup and retained-queue flush.

## Example

1. Account A fails freshness/connect; health is queued.
2. Account B streams successfully; settlement commits and sets `settled`.
3. Deferred flush awaits `_handle_stream_error` for A.
4. Cancel arrives during that await.
5. Cleanup still sees `settled` and finishes the retained deferred penalty.

## Cancellation idempotency

Each queued deferred health entry is applied in an owned task awaited via
`_await_task_deferring_cancellation`. Cancel mid-write waits for that entry to
finish (or log failure), then pops it, then re-raises. That prevents cleanup
from replaying a half-applied tuple and double-incrementing `error_count`
through `_handle_stream_error` / `record_errors`.

## Persistence ownership

Once ordering-sensitive settlement transfers from the request, a failed
primary attempt plus failed immediate release leaves reservation recovery with
the tracked retrying release cleanup. Deferred health remains unapplied for
that unconfirmed request path. If cancellation arrives while the immediate
fallback is still running and that fallback confirms, the settlement task
reports the confirmed release instead of treating cancel as failure. That
avoids a second unbounded retry after the reservation is already released and
lets deferred health flush. If cancellation leaves later post-settlement
health entries for a detached flush, the flush is persistence work and the
graceful shutdown drain waits for it.

Deferred route backoffs and queued stream-health penalties are independent
post-settlement work. If persisting a route backoff fails, cleanup still
attempts queued stream-health penalties before surfacing that backoff failure.
When cancellation has already completed one queue entry, later entries transfer
to the tracked cancel-safe flush even if final backoff retry also fails.
