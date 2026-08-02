# Tasks

## 1. Idle release

- [x] 1.1 Release the session's account stream lease when its last in-flight turn detaches (no queued requests, admission waiters, or pending requests), leaving the session alive for reuse.
- [x] 1.2 Keep session-close settlement untouched (release is idempotent; a released-idle session has nothing to settle at close).
- [x] 1.3 Release the lease on the grouped terminal-error settlement path too (multiple detached follow-ups popped and finalized together return before the single terminal path's release hook).

## 2. Turn-admission reacquisition

- [x] 2.1 Reacquire a lease under `session.pending_lock` before a turn is counted into the session queue.
- [x] 2.2 Raise the standard HTTP 429 `account_stream_cap` envelope on denial so the recoverable capacity wait applies.
- [x] 2.3 Re-check session closure after the acquire await; release the fresh lease and fail with the closed-bridge envelope instead of installing it on a closed session.
- [x] 2.4 Register the submit as an admission waiter atomically with the first reacquire so stale finalizers cannot idle-release the fresh lease before queue admission; unregister on prewarm failure and queue-full rejection.
- [x] 2.5 Pass the turn's usage-budget token estimate into the reacquired lease, matching initial selection and reconnect, so capacity-weighted routing pressure sees reused-session turns.
- [x] 2.6 Retire a session closed during prewarm when failed-submit cleanup removes its final admission waiter.
- [x] 2.7 Defer cancellation until prewarm-failure cleanup removes the admission waiter and settles the lease.
- [x] 2.8 Cover the final lease check with failed-submit cleanup so exceptions cannot leak admission waiters.
- [x] 2.9 Serialize reconnect lease replacement with idle-session reacquisition and release the losing lease.
- [x] 2.10 Defer cancellation until a detached lease acquired during a close race is released.
- [x] 2.11 Defer cancellation through idle and reconnect-replacement lease settlement after session ownership is cleared.

## 3. Tests

- [x] 3.1 Idle release, busy/closed retention, reacquisition, denial envelope, and held-lease no-op coverage.
- [x] 3.2 Grouped terminal-error settlement of detached requests releases the abandoned session's lease.
- [x] 3.3 Close racing an in-flight reacquisition releases the fresh lease and fails with the closed-bridge envelope.
- [x] 3.4 Stale finalizer during prewarm cannot release the reacquired lease; queue-full rejection unregisters the admission waiter and retains the busy session's lease.
- [x] 3.5 Reacquisition passes the turn's usage-budget token estimate to the lease.
- [x] 3.6 Prewarm failure after reader-side closure retires the session and releases its stream lease.
- [x] 3.7 Repeated cancellation during prewarm cannot interrupt waiter and lease cleanup.
- [x] 3.8 Final lease-check failure removes the admission waiter and releases the idle lease.
- [x] 3.9 Reconnect racing reacquisition retains one lease and releases the other.
- [x] 3.10 HTTP bridge terminal completion releases a capped account slot so a second warm session is admitted before idle TTL expiry.
- [x] 3.11 Cancellation during close-race settlement cannot interrupt detached lease release.
- [x] 3.12 Reader cancellation cannot interrupt idle release, and reconnect cancellation cannot interrupt replaced-lease settlement.
