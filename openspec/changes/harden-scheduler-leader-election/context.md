# scheduler-coordination context (to be synced to openspec/specs/scheduler-coordination/context.md)

## Purpose

Seven singleton schedulers (usage refresh, api-key limit reset, model refresh, sticky-session cleanup, quota planner, auth guardian, automations) must run on exactly one replica at a time. They coordinate through a single-row lease in the `scheduler_leader` table (id = 1): a conditional upsert takes the lease only when it is expired or already held by the caller, and the statement's affected rowcount decides the winner.

The `rate_limit_reset_credits` scheduler is intentionally **per-process** (see `rate-limit-reset-credits`); it does not gate on the lease.

## Clock domains

- **PostgreSQL**: both the stored expiry (`now() + make_interval(secs => :ttl)`) and the takeover predicate (`expires_at < now()`) are evaluated on the database clock. Replica wall clocks never participate, so NTP skew between replicas cannot steal a live lease.
- **SQLite**: a shared SQLite file implies a single host, so the host clock is a single clock domain. The writer binds `datetime.now(UTC)` on both sides of the comparison. Only `leader_election.py` writes this row; manual UPDATEs with a different datetime format could confuse the string comparison — do not hand-edit `scheduler_leader`.

## Residual overlap (accepted risk)

There are no fencing tokens. When a lease is lost mid-task, the old leader's heartbeat notices on its next renewal (interval `max(1, ttl // 3)` seconds) and cancels the in-flight body, so two leaders can overlap for at most roughly one renew interval plus cancellation latency. DB-level idempotency guards backstop the critical writes during that window: the quota planner's unique `idempotency_key`, the automations scheduler's unique `slot_key`, and warmup's `SELECT ... FOR UPDATE`.

Cancellation flows through each scheduler body as `asyncio.CancelledError`; bodies use per-tick sessions via `get_background_session` context managers, so half-done DB work rolls back with the session.

## Configuration and operations

- `CODEX_LB_LEADER_ELECTION_ENABLED` defaults to **true**. Disabling it makes every replica treat itself as leader — only safe for a single-instance deployment. Multi-worker SQLite deployments that previously (and silently) ran N leaders now get exactly one; that is the fix, not a regression.
- `CODEX_LB_LEADER_ELECTION_TTL_SECONDS` defaults to **60** (minimum 5). The renew interval is `ttl // 3` (minimum 1s), so the shipped Helm value `ttl=30` is valid (renew 10s). Long-running tasks such as the 300s quota-planner tick are safe at any TTL because the lease is renewed while the body runs.
- Graceful shutdown deletes the lease row after all schedulers stop, so a follower takes over on its next tick. A crash without release pauses singleton scheduling for at most the TTL (60s by default, previously 600s).
- Example: two replicas on one PostgreSQL database with default env — replica A wins the lease on its first tick and heartbeats while work runs; replica B's upserts affect 0 rows and it skips its ticks. When A is redeployed, its shutdown deletes the row and B acquires within one tick interval.
