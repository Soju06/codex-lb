# scheduler-coordination context (to be synced to openspec/specs/scheduler-coordination/context.md)

## Purpose

Seven singleton schedulers (usage refresh, api-key limit reset, model refresh, sticky-session cleanup, quota planner, auth guardian, automations) must run on exactly one replica at a time. They coordinate through a single-row lease in the `scheduler_leader` table (id = 1): a conditional upsert takes the lease only when it is expired or already held by the caller, and the statement's affected rowcount decides the winner.

The `rate_limit_reset_credits` scheduler is intentionally **per-process** (see `rate-limit-reset-credits`); it does not gate on the lease.

## Clock domains

- **PostgreSQL**: both the stored expiry (`clock_timestamp() + make_interval(secs => :ttl)`) and the takeover predicate (`expires_at < now()`) are evaluated on the database clock. Replica wall clocks never participate, so NTP skew between replicas cannot steal a live lease. The stored expiry uses `clock_timestamp()` (actual statement-execution time), not `now()`/`transaction_timestamp()` (fixed at transaction start): overlapping renewals on the shared leader-election singleton queue on the `scheduler_leader` row lock, so a renewal that captured `now()` before it blocked on the lock could commit after a newer renewal and write an earlier `expires_at`, shortening the lease below the leader's locally tracked deadline. `clock_timestamp()` is evaluated after the lock is acquired, in commit order, so `expires_at` only moves forward. The takeover predicate stays on the transaction snapshot clock (`now()`), which is the conservative choice for a point-in-time takeover read.
- **SQLite**: a shared SQLite file implies a single host, so the host clock is a single clock domain. The writer binds `datetime.now(UTC)` on both sides of the comparison. Only `leader_election.py` writes this row; manual UPDATEs with a different datetime format could confuse the string comparison — do not hand-edit `scheduler_leader`.

## Residual overlap (accepted risk)

There are no fencing tokens. When a lease is lost mid-task, the old leader's heartbeat notices on its next renewal (interval `max(1, ttl // 3)` seconds, each attempt time-boxed to `ttl / 6` so a hung DB call cannot extend leadership past the TTL) and cancels the in-flight body. The gate awaits the cancelled body for a bounded grace (5s) and then detaches it, so the gate itself stops within one renew interval plus the grace. DB-level idempotency guards backstop the critical writes during that window: the quota planner's unique `idempotency_key`, the automations scheduler's unique `slot_key`, and warmup's `SELECT ... FOR UPDATE`.

Cancellation flows through each scheduler body as `asyncio.CancelledError`; bodies use per-tick sessions via `get_background_session` context managers, so half-done DB work rolls back with the session.

Two gated bodies deliberately shield singleton refresh work against cancellation and may drain it past lease loss, concurrently with the new leader:

- **Usage refresh** joins `_USAGE_REFRESH_SINGLEFLIGHT` (`app/modules/usage/updater.py`), whose tasks are shared with request-path callers on the same replica, so the gate must not cancel through the shield. A drained usage refresh is idempotent: it re-reads upstream usage and writes the latest snapshot, so a concurrent refresh by the new leader produces the same data (the cost is one redundant upstream call, bounded by the HTTP client timeout).
- **Auth Guardian** shields `AuthManager.ensure_fresh` (`app/core/auth/guardian.py`), which runs inside `_REFRESH_SINGLEFLIGHT` — also joined by request-path token refreshes — so cancelling through would abort refreshes that in-flight requests on the same replica are waiting on. The drain is bounded by the refresh HTTP timeout; cross-replica serialization of token refreshes themselves is the subject of the separate `serialize-cross-replica-token-refresh` change and is out of scope here.

## Configuration and operations

- `CODEX_LB_LEADER_ELECTION_ENABLED` defaults to **true**. Disabling it makes every replica treat itself as leader — only safe for a single-instance deployment. Multi-worker SQLite deployments that previously (and silently) ran N leaders now get exactly one; that is the fix, not a regression.
- Exception: the **Auth Guardian** refuses to run at all in a multi-replica ring with leader election disabled (`build_auth_guardian_scheduler` forces `enabled=false` and logs a warning). It force-refreshes OAuth tokens, and concurrent force refreshes across replicas can invalidate rotated refresh tokens — "everyone is leader" would be actively harmful there, while losing proactive refresh only delays refreshes until the request path performs them lazily.
- `CODEX_LB_LEADER_ELECTION_TTL_SECONDS` defaults to **60** (minimum 5). The renew interval is `ttl // 3` (minimum 1s), so the shipped Helm value `ttl=30` is valid (renew 10s). Long-running tasks such as the 300s quota-planner tick are safe at any TTL because the lease is renewed while the body runs.
- Graceful shutdown deletes the lease row after all schedulers stop, so a follower takes over on its next tick. Before deleting, `release()` waits a bounded grace (5s) for any gated body that was detached still draining shielded refresh work; if one is still running after the grace, the early release is skipped and the lease simply expires after its TTL — deleting the row there would let a follower become leader while the old process may still be refreshing tokens or usage as leader. A crash without release pauses singleton scheduling for at most the TTL (60s by default, previously 600s).
- Example: two replicas on one PostgreSQL database with default env — replica A wins the lease on its first tick and heartbeats while work runs; replica B's upserts affect 0 rows and it skips its ticks. When A is redeployed, its shutdown deletes the row and B acquires within one tick interval.
