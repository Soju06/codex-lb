# Tasks

## 1. Lock Discipline

- [x] 1.1 Add `_http_bridge_fair_share_threshold_pct` (lock-free snapshot resolve) and a `fair_share_threshold_pct` parameter to `_ensure_http_bridge_session_stream_lease_locked`; keep the inline resolve only as a fallback for lock-free callers.
- [x] 1.2 Resolve the snapshot before the reacquire's `pending_lock` acquisition in `_submit_http_bridge_request` and pass it through, so no settings/DB await runs under the lock; the post-prewarm reacquire reuses the same snapshot (a fresh TTL-expired refresh there could only stall an otherwise admissible request).
- [x] 1.3 Register the admission waiter under a brief lock BEFORE the settings resolve so the idle sweeper and close-time retire cannot evict the session while the snapshot resolves; a reacquire failure hands the registered waiter to interruption cleanup to unwind.
- [x] 1.4 Resolve the snapshot only when the reacquire can actually run (`needs_stream_lease` snapshotted under the registration lock): a session already holding its lease never depended on a settings read to admit a turn.
- [x] 1.5 Count registered admission waiters as pending work in `_http_bridge_pending_count_nowait` so capacity LRU eviction and shutdown drain cannot treat a session as idle while a turn is suspended on the pre-lock resolve.
- [x] 1.6 Include `admission_waiter_count == 0` in the drain-retirement predicate so retirement cannot close the bridge under a registered waiter suspended on the pre-lock resolve; a deferred retirement re-runs on the turn's own drain triggers, and an admission-failed waiter's cleanup releases the idle lease with idle-TTL close as the backstop.

## 2. Statement Bound

- [x] 2.1 Add the fixed `_POSTGRES_COMMAND_TIMEOUT_SECONDS` application constant and set asyncpg `command_timeout` in `_postgres_async_connect_args` (PostgreSQL only; Alembic's synchronous engine unaffected).

## 3. Regression Coverage

- [x] 3.1 Reacquire with a provided snapshot never awaits the settings cache and forwards the threshold to lease acquisition.
- [x] 3.2 Product path: a stalled settings-cache refresh stalls the submit BEFORE `pending_lock` — the lock stays acquirable — and cancelling the stalled submit leaves no admission-waiter or lease residue. Sabotage-verified: fails against the old in-lock resolve.
- [x] 3.3 Engine connect-args tests assert the `command_timeout` bound alongside the UTC pin.

## 4. Verification

- [x] 4.1 Run the idle-leases and db-session unit suites plus lint (ruff) and type check (ty) on touched files.
- [x] 4.2 Strict OpenSpec validation.
