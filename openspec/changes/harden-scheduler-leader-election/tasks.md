# Tasks

- [x] 1. Create OpenSpec change artifacts (proposal, design, tasks, delta specs for ADDED `scheduler-coordination` and MODIFIED `usage-refresh-policy`, capability context) and pass `openspec validate harden-scheduler-leader-election --strict`
- [x] 2. Rewrite `app/core/scheduling/leader_election.py`: dialect detection from the session bind; per-dialect acquire SQL (PostgreSQL uses `now()`/`make_interval` server-side, SQLite binds Python UTC datetimes); rowcount-based win detection replacing the post-commit SELECT; remove both `"sqlite"` URL-substring bypasses
- [x] 3. Wire `renew()` (rowcount == 1, demote on 0); add `release()` (DELETE WHERE id = 1 AND leader_id = :me); add `run_if_leader(fn)` with a heartbeat at `max(1, ttl // 3)` seconds cancelling the body on lease loss or 2 consecutive renew errors
- [x] 4. Convert the 7 gate sites to `run_if_leader` and update their `_LeaderElectionLike` protocols: `app/core/usage/refresh_scheduler.py`, `app/modules/api_keys/reset_scheduler.py`, `app/core/openai/model_refresh_scheduler.py`, `app/modules/sticky_sessions/cleanup_scheduler.py`, `app/modules/quota_planner/scheduler.py`, `app/core/auth/guardian.py`, `app/modules/automations/scheduler.py`
- [x] 5. `app/main.py` lifespan finally-block: release the lease after the last scheduler stops, guarded by try/except + `asyncio.wait_for`
- [x] 6. `app/core/config/settings.py`: `leader_election_enabled` default True; `leader_election_ttl_seconds` default 60 with `Field(ge=5)`
- [x] 7. Fix `openspec/specs/quota-phase-planner/context.md` wording; write the scheduler-coordination capability context (change-level, to move on sync)
- [x] 8. Rewrite `tests/integration/test_multi_replica.py` leader test to assert exactly one winner on SQLite; add takeover-after-release, takeover-after-expiry, and renew-demotion tests using two `LeaderElection` instances over one DB
- [x] 9. Add a scheduler-level two-replica integration test asserting the automations tick body runs exactly once across two concurrently ticking instances
- [x] 10. Add `run_if_leader` lifecycle tests: heartbeat keeps a follower out during a long body; stealing the lease cancels the body within one renew interval; release lets a second instance acquire immediately
- [x] 11. Rewrite `tests/unit/test_leader_election.py` for the new API (rowcount-based, dialect-driven); update scheduler unit-test leader fakes and settings default assertions
- [x] 12. Run targeted pytest, ruff check + format, and `openspec validate` until green
- [x] 13. Review follow-up: time-box each renewal attempt (`asyncio.wait_for`, `ttl / 6`) and demote on a passed local lease deadline so a hung DB cannot extend leadership past the TTL
- [x] 14. Review follow-up: bound the post-cancel await of the gated body to a 5s grace and detach (with logged outcome) bodies draining shielded singleflight refreshes; document the residual overlap and its safety argument in `context.md`
- [x] 15. Review follow-up: narrow the disabled-election contract for Auth Guardian (multi-replica ring without election disables the guardian) and emit an operator-visible warning from `build_auth_guardian_scheduler`
