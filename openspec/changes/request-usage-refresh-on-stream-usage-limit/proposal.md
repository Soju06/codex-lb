# Request an immediate coalesced usage refresh on streamed usage-limit failures

## Why

When an upstream stream fails with `usage_limit_reached`, `_handle_stream_error`
marks the account `rate_limited` (status, `blocked_at`, `reset_at`) but writes no
usage evidence. The pool-exhaustion predicate requires both a blocked status and a
usage row at or above 100 %, so a pool whose last account just hit its limit keeps
reporting `no_accounts` (or a local wait) instead of the structured
`usage_limit_reached` failure with its `resets_at` until the background scheduler
next refreshes the account -- up to one `usage_refresh_interval_seconds` plus the
freshness gate, which only bypasses `rate_limited` rows after their `reset_at`
elapses. That lag is the gap issue #2123 (WP-F) closes before any Model Source
overflow decision depends on the predicate.

The only immediate-refresh API today, `UsageUpdater.force_refresh`, is unsuitable
from a streaming request: it waits for an in-flight refresh and then starts
another (no coalescing under a 429 storm) and it mutates the caller's `Account`
ORM instance, which belongs to the request session.

## What Changes

- `UsageUpdater.request_refresh(account_id)` returns a background refresh
  coroutine (or `None` when suppressed) that loads the account from a fresh
  background row, bypasses the freshness gate, joins any in-flight owned-session
  refresh of the same account (`join_existing=True`; never queues a successor
  fetch), and never touches a caller's `Account`. Repeats within a fixed 15 s
  debounce window, disabled usage refresh, and accounts in auth cooldown return
  `None`.
- `_handle_stream_error` requests that refresh after `mark_rate_limit` when the
  stream error code is exactly `usage_limit_reached`, scheduling it through
  `ProxyService._schedule_cancel_safe_cleanup` (tracked task
  `proxy-request_usage_refresh-<request_id>`). Plain `rate_limit_exceeded`
  throttling and quota codes are excluded: throttling is transient, and quota
  codes already pin `used_percent=100` in runtime state.
- No overflow behaviour is wired; the change is valuable stand-alone.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: streamed usage-limit failures request an immediate,
  coalesced, debounced, fresh-row, tracked usage refresh.

## Impact

- The pool reports `usage_limit_reached` with the correct `resets_at` on the next
  selection after the >= 100 % row lands, seconds after the first upstream 429
  instead of up to one scheduler interval later.
- A 429 storm on one account produces at most one upstream `/wham/usage` fetch
  per debounce window per process; concurrent requests join the in-flight fetch.
- No new settings (PRINCIPLES.md P2): the debounce is a constant. No new SQL;
  existing background repositories are reused (SQLite/PostgreSQL parity).
- `CancelledError` propagates out of the tracked refresh; only `Exception` is
  logged and swallowed inside it.
