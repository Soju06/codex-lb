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

## 3. Tests

- [x] 3.1 Idle release, busy/closed retention, reacquisition, denial envelope, and held-lease no-op coverage.
- [x] 3.2 Grouped terminal-error settlement of detached requests releases the abandoned session's lease.
- [x] 3.3 Close racing an in-flight reacquisition releases the fresh lease and fails with the closed-bridge envelope.
- [x] 3.4 Stale finalizer during prewarm cannot release the reacquired lease; queue-full rejection unregisters the admission waiter and retains the busy session's lease.
