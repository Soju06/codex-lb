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
