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

Separately, a forked thread that replays reasoning ciphertext minted for another
account dies on ChatGPT's HTTP 400 and is undetectable from ids; the residual has
to be sized before overflow ships, so upstream 400 rejections that reference
reasoning items are counted.

## What Changes

- `UsageUpdater.request_refresh(account_id)` returns a background refresh
  coroutine (or `None` when suppressed) that loads the account from a fresh
  background row, bypasses the freshness gate, and never touches a caller's
  `Account`. Usage refreshes run on two per-account singleflight lanes -- the
  scheduler and `force_refresh` on the bare account key, the rate-limit payload
  and fleet paths on the owned-session key -- so the requested refresh first
  joins a scheduler refresh already in flight and otherwise runs on the
  owned-session key with `join_existing=True` (never queues a successor fetch).
  Repeats within a fixed 15 s debounce window, disabled usage refresh, and
  accounts in auth cooldown return `None`.
- `_handle_stream_error` requests that refresh after `mark_rate_limit` when the
  stream error code is exactly `usage_limit_reached`, scheduling it through
  `ProxyService._schedule_cancel_safe_cleanup` (tracked task
  `proxy-request_usage_refresh-<request_id>`). Plain `rate_limit_exceeded`
  throttling and quota codes are excluded: throttling is transient, and quota
  codes already pin `used_percent=100` in runtime state.
- `codex_lb_upstream_reasoning_replay_400_total` counts upstream HTTP 400
  rejections (or code-less `invalid_request_error` frames) whose message
  references reasoning. Observation only: classification, account health and
  failover are unchanged, and the counter is a no-op without `prometheus_client`.
- No overflow behaviour is wired; the change is valuable stand-alone.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: streamed usage-limit failures request an immediate,
  coalesced, debounced, fresh-row, tracked usage refresh.
- `proxy-runtime-observability`: upstream reasoning-replay rejections are
  counted.

## Impact

- The pool reports `usage_limit_reached` with the correct `resets_at` on the next
  selection after the >= 100 % row lands, seconds after the first upstream 429
  instead of up to one scheduler interval later.
- A 429 storm on one account produces at most one upstream `/wham/usage` fetch
  per debounce window per process; concurrent requests join the in-flight fetch,
  including a scheduler refresh of the same account that is already running. The
  debounce and the singleflight lanes are process-local, so under a cross-replica
  storm each replica fetches once per window (the scheduler lane does not join
  request lanes; that pre-existing asymmetry is unchanged).
- No new settings (PRINCIPLES.md P2): the debounce is a constant. No new SQL;
  existing background repositories are reused (SQLite/PostgreSQL parity).
- `CancelledError` propagates out of the tracked refresh; only `Exception` is
  logged and swallowed inside it.
