# Tasks

## 1. Idle release

- [x] 1.1 Release the session's account stream lease when its last in-flight turn detaches (no queued requests, admission waiters, or pending requests), leaving the session alive for reuse.
- [x] 1.2 Keep session-close settlement untouched (release is idempotent; a released-idle session has nothing to settle at close).

## 2. Turn-admission reacquisition

- [x] 2.1 Reacquire a lease under `session.pending_lock` before a turn is counted into the session queue.
- [x] 2.2 Raise the standard HTTP 429 `account_stream_cap` envelope on denial so the recoverable capacity wait applies.

## 3. Tests

- [x] 3.1 Idle release, busy/closed retention, reacquisition, denial envelope, and held-lease no-op coverage.
