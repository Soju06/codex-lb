# Harden scheduler leader election

## Why

The `scheduler_leader` lease gating seven singleton schedulers is unsound in every multi-replica configuration: it unconditionally claims leadership on SQLite (`uvicorn --workers N` runs N leaders), compares lease expiry across replica wall clocks (skew steals live leases), never renews during long tasks (`renew()` is dead code that also ignores rowcount; the shipped Helm TTL of 30s is smaller than the 300s planner tick), never releases the lease on graceful shutdown (every deploy stalls singleton scheduling for up to the 600s TTL), and detects SQLite by substring match over the database URL. Consequences: duplicate concurrent token force-refreshes that mark healthy accounts REAUTH_REQUIRED, double-spent warmup quota, and N-fold upstream usage fetches. No OpenSpec capability owns the lease contract even though usage-refresh-policy cites it.

## What Changes

- Rewrite `app/core/scheduling/leader_election.py`:
  - Real arbitration on SQLite via the same conditional upsert used for PostgreSQL (atomic under SQLite's single-writer lock); the everyone-is-leader bypass is removed.
  - Backend selection from the engine dialect (`session.get_bind().dialect.name`), never from URL text.
  - Single clock domain: PostgreSQL computes both the new expiry (`now() + make_interval(...)`) and the takeover predicate (`expires_at < now()`) on the database clock; SQLite binds host-clock datetimes (a shared SQLite file implies one host).
  - Win/renew detection via the statement's affected rowcount (replaces the racy post-commit SELECT); `renew()` demotes on rowcount 0.
  - New `run_if_leader(fn)`: acquires, heartbeats every `max(1, ttl // 3)` seconds while the body runs, and cancels the body when the lease is lost or after 2 consecutive renewal errors.
  - New `release()`: deletes the lease row we hold.
- Convert the seven scheduler gate sites to `run_if_leader` and update their `_LeaderElectionLike` protocols.
- Release the lease in the `app/main.py` lifespan finally-block after all schedulers stop.
- Settings: `leader_election_enabled` defaults to true; `leader_election_ttl_seconds` defaults to 60 with a minimum of 5.
- New `scheduler-coordination` capability owning the lease contract; `usage-refresh-policy`'s "Multi-replica leader guard" references it; `quota-phase-planner/context.md` wording fixed.
- `replica-operations` MODIFIED delta: its SQLite requirement no longer claims the lease is bypassed (SQLite now arbitrates the durable lease via the atomic conditional upsert, so exactly one process wins), and its multi-replica requirement reflects that `CODEX_LB_LEADER_ELECTION_ENABLED` defaults to `true` — keeping the main SSOT consistent with `scheduler-coordination` on archive.
- No migration: the existing `scheduler_leader` table suffices.

## Non-goals

- Fencing tokens re-verified before every side effect (DB-level idempotency guards backstop the bounded residual overlap; documented in context).
- The `rate_limit_reset_credits` scheduler, which is intentionally per-process.
- The guardian's static-ring multi-replica guard (owned by serialize-cross-replica-token-refresh).
