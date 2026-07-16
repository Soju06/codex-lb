# Design: harden scheduler leader election

## Decisions

### 1. Real arbitration on SQLite (no bypass)

The same conditional upsert used for PostgreSQL —
`INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at) VALUES (1, ...) ON CONFLICT (id) DO UPDATE SET ... WHERE scheduler_leader.expires_at < :now OR scheduler_leader.leader_id = :leader_id`
— is atomic under SQLite's single-writer file lock (SQLite >= 3.24 upsert), so SQLite runs the identical arbitration instead of short-circuiting to "everyone is leader". The codebase already treats multi-process SQLite as supported (`DatabaseRateLimiter`'s atomic-INSERT pattern, NullPool + busy_timeout).

Rejected alternatives:
- Startup guard refusing schedulers when `workers > 1`: cannot detect separate containers sharing one volume, and gratuitously weaker than what the DB already provides.
- OS `flock` sidecar file: does not span containers that share only the DB volume, and adds a second coordination medium.

### 2. Single clock domain

On PostgreSQL both the new expiry (`clock_timestamp() + make_interval(secs => :ttl)`) and the takeover predicate (`expires_at < now()`) are computed server-side, so one clock (the database's) arbitrates and inter-replica wall-clock skew cannot steal a live lease. The stored expiry deliberately uses `clock_timestamp()` (actual statement-execution time) rather than `now()`/`transaction_timestamp()` (fixed at transaction start): overlapping renewals on the shared leader-election singleton queue on the `scheduler_leader` row lock, and a renewal that computed its expiry from a pre-lock `now()` could commit after a newer renewal and write an earlier `expires_at`, shortening the lease below the leader's locally tracked deadline. Because the row lock serializes writers, `clock_timestamp()` is evaluated in commit order, so the lease can only move forward. The takeover predicate stays on the transaction snapshot clock (`now()`): the takeover decision is a single point-in-time read and staying on the snapshot is conservative (a waiter never over-eagerly steals a lease that was refreshed while it was blocked on the lock). On SQLite, Python `datetime.now(UTC)` is bound on both sides: a shared SQLite file implies a single host, hence a single clock domain; only `leader_election.py` ever writes the row, so ISO-serialized datetime comparisons stay format-consistent.

Rejected: monotonic lease-version counters — needs a schema migration and still requires a clock somewhere for expiry; the DB clock is simpler and the DB is already the shared medium.

### 3. Rowcount-based win detection

`result.rowcount == 1` on the upsert/UPDATE decides the winner on both dialects (asyncpg and aiosqlite report it correctly for `ON CONFLICT DO UPDATE ... WHERE`), replacing the post-commit SELECT that could observe a competitor acquiring between commit and read. `renew()` is wired for real: rowcount 0 demotes (`_is_leader = False`).

### 4. Holding the lease during work: `run_if_leader`

`run_if_leader(fn)` acquires, spawns a heartbeat renewing every `max(1, ttl // 3)` seconds while the body runs, and cancels the body when renewal reports the lease lost (or after 2 consecutive renewal errors). Each renewal attempt is time-boxed with `asyncio.wait_for` to `ttl / 6` and the heartbeat tracks a local monotonic lease deadline, so a hung database connection demotes the holder no later than the TTL instead of extending leadership by the pool timeout. After cancellation the gate awaits the body for a bounded grace (5s) and then detaches it with a logging done-callback: bodies that shield singleflight refreshes (usage refresh, Auth Guardian's `ensure_fresh`) share those tasks with request-path callers, so cancelling through the shield is not safe; the residual drain is bounded by the upstream operation's own timeout and documented in `context.md`.

Rejected alternatives:
- Fencing tokens re-verified before every side effect across seven schedulers: requires a migration (new fence column chaining onto an uncommitted head) plus deep surgery in every scheduler, while DB-level idempotency guards (quota-planner unique `idempotency_key`, automations unique `slot_key`, warmup `SELECT ... FOR UPDATE`) already backstop the critical writes. Residual bounded overlap is documented in `context.md`.
- Continuous per-process background heartbeat with schedulers reading an in-memory flag: cleaner steady-state but a much larger refactor of scheduler lifecycles for marginal benefit.

### 5. Release on graceful shutdown

`DELETE FROM scheduler_leader WHERE id = 1 AND leader_id = :me` from the lifespan finally-block after all schedulers stop (so no local tick re-acquires), following the bridge-ring `mark_stale()` shutdown precedent, wrapped in try/except + `asyncio.wait_for` so release failure never blocks shutdown.

Rejected: setting `expires_at = now()` — equivalent, but leaves a row the SQLite comparison path must keep parsing; DELETE is unambiguous.

### 6. Default flip

`leader_election_enabled` defaults to true and `leader_election_ttl_seconds` drops 600 -> 60 (renew ~20s; the Helm chart's ttl=30 remains valid with renew 10s). With real SQLite arbitration there is no backend where enabled-by-default is unsafe; single-replica overhead is one tiny conditional upsert per scheduler tick; error paths still default to non-leader. Crash-without-release now costs at most 60s of scheduler pause instead of 600s.

### 7. Dialect detection

`session.get_bind().dialect.name == "sqlite"` chooses the SQL flavor (never a bypass), matching the `db_rate_limiter.py` pattern and structurally eliminating the URL-substring bug; no URL parsing remains in the module.

### 8. No migration

The existing `scheduler_leader` table (id INTEGER PK, leader_id VARCHAR(100), acquired_at/expires_at DateTime(timezone=True)) suffices. Avoiding a fencing column also avoids chaining a revision onto the in-flight usage-rollup migration head.

## Deviations from the design document

- The scheduler-level two-replica integration test injects distinct `LeaderElection` instances by monkeypatching the automations scheduler's module-level `_get_leader_election` with a per-call sequence (the scheduler has no constructor injection point for leader election); the tick body is replaced with a counter. The gate itself (`run_if_leader` over the shared DB) is exercised for real.
- The `scheduler-coordination` capability context lives at `openspec/changes/harden-scheduler-leader-election/context.md` (change level). It should be moved to `openspec/specs/scheduler-coordination/context.md` when the delta is synced, because the capability's main `spec.md` does not exist until sync.
- The shutdown-release product path is covered by an integration test of `release()` plus follower takeover; a full app-lifespan smoke was left out to keep the test suite light (the `app/main.py` wiring is four lines mirroring the ring `mark_stale()` block).
