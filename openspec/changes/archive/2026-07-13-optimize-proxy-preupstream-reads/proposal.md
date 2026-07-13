## Why

Interactive Codex turns pay avoidable database round trips before the first upstream byte: the proxy API-key auth cache expires every 2 seconds (shorter than typical think-time, so effectively every turn re-reads the key row) even though key mutations already propagate cross-instance via the cache-invalidation poller in ~0.5 s; every sticky-session (re)assignment runs an upsert as four round trips (INSERT ON CONFLICT + COMMIT + re-SELECT + `session.refresh`); and account selection issues three usage queries through `asyncio.gather` on one shared `AsyncSession` — unsafe on asyncpg and with zero parallelism gain.

## What Changes

- Raise the proxy API-key auth cache TTL from 2 s to 60 s. Revocation/update latency is unchanged (poller-driven invalidation remains the binding mechanism, ~0.5 s cross-instance); the TTL is only the backstop when the poller is broken.
- Collapse the sticky-session upsert to a single `INSERT ... ON CONFLICT ... RETURNING` statement (plus commit) — identical row contents and `updated_at` bump semantics, three fewer round trips on the sticky hot path.
- Replace the same-session `asyncio.gather` over the three selection-input usage reads with sequential awaits (correctness: one `AsyncSession` must not run concurrent statements).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: hot-path contracts extended — proxy API-key auth caching MUST be invalidation-driven with a TTL backstop of at least 60 s; sticky-session upserts MUST complete in one statement; selection-input reads MUST NOT execute concurrently on a shared session.

## Impact

- **Code**: `app/core/auth/api_key_cache.py`, `app/modules/proxy/sticky_repository.py`, `app/modules/proxy/load_balancer.py`. No schema change, no API change.
- **Behavior**: none observable — auth results, sticky rows, and selection inputs are byte-identical; only latency and statement counts change.
