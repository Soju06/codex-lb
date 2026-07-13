## Context

Proxy hot-path audit findings: the API-key auth cache TTL (2 s) is shorter than typical Codex CLI think-time, so interactive turns miss the cache and re-read the key row (plus lazy-reset writes) before every upstream call; sticky upserts cost 4 round trips inline pre-upstream; and three selection-input usage reads run through `asyncio.gather` on one `AsyncSession`, which the project's own async rules forbid.

## Goals / Non-Goals

**Goals:** fewer pre-upstream DB round trips on interactive turns with byte-identical behavior; remove the shared-session concurrency hazard.

**Non-Goals:** upstream-route resolution caching and stream-end write detachment (separate changes — routing and settlement invariants deserve their own review scope); `pool_pre_ping` tuning; rate-limit header cache changes.

## Decisions

- **TTL 60 s, not removal**: the poller (0.5 s) is the real invalidation path — registered in `app/main.py` (`cache_poller.on_invalidation(NAMESPACE_API_KEY, get_api_key_cache().clear)`) and bumped by every key mutation site. The TTL stays as a bounded backstop for a broken poller rather than being removed outright. Per-key expiry (`expires_at`) is still checked on every cache hit, so key expiration is unaffected by the TTL.
- **RETURNING upsert**: `expire_on_commit=False` on both session factories keeps the returned ORM entity readable after commit; PostgreSQL and SQLite (≥ 3.35) both support `ON CONFLICT ... RETURNING` for insert and update arms. The happy-path `updated_at` bump is intentionally kept per-request (it feeds the TTL purge cutoff); only the redundant re-select/refresh round trips are removed.
- **Sequential awaits**: per-connection drivers serialize statements anyway; gather on one session risks `InterfaceError`/state corruption on asyncpg for zero gain.

## Risks / Trade-offs

- [Poller outage extends stale-auth window from 2 s to 60 s] → the poller is process-critical infrastructure already (firewall allowlist relies on it); 60 s remains a bounded backstop and per-key `expires_at` enforcement is unaffected.

## Migration Plan

Code-only; rollback = revert.

## Open Questions

None.
