# Coalesce api_keys.last_used_at writes behind a periodic flush

## Why

Every settled proxy request currently issues `UPDATE api_keys SET last_used_at = ...` inside the reservation-settlement transaction, so the column costs one row write (and its share of an fsync) per request. Production `pg_stat_statements` over a 10-minute window shows 616 executions of this UPDATE totaling 44.6 s — for only 7 active keys — making it a top contributor to the >500 ms slow-query population, 93% of which is write/fsync-path. The value is purely informational: the only consumer is the dashboard API response field (`lastUsedAt`), which the current frontend does not even render, and no routing, ordering, or enforcement logic reads it. Per-request durability for a display-only timestamp is wasted fsync budget.

## What Changes

- Settlement paths stop writing `last_used_at` in the settlement transaction. Instead they record the touch into a process-local in-memory coalescer (`{api_key_id: max(observed used-at)}`; single asyncio event loop, no cross-thread access).
- A replica-local periodic flusher (constant 30-second interval, mirroring the existing lifespan scheduler start/stop pattern; NOT leader-gated) swaps the pending map and folds it into the database in one transaction — one guarded UPDATE per touched key. Every flush write carries monotonic greatest-wins semantics (`WHERE last_used_at IS NULL OR last_used_at < :new`, the dialect-portable equivalent of `GREATEST(coalesce(last_used_at, epoch), :new)`), so out-of-order flushes from multiple replicas can never move the column backwards and no leader election is needed.
- Graceful shutdown performs a final flush after proxy settlement tasks drain. A hard crash may lose at most one flush interval (≤30 s) of `last_used_at` freshness — an accepted trade documented in the spec delta (the column is display-only).
- The legacy `record_usage` path (`increment_limit_usage`) stops writing `last_used_at` inline and records through the same coalescer.
- No new settings: the flush interval is a code constant per the reduce-settings-surface policy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: `last_used_at` accounting changes from per-request synchronous durability to write-behind coalescing with bounded (≤30 s + one flush write) staleness and greatest-wins flush semantics. This capability owns the API-key accounting contract that `last_used_at` belongs to.

## Impact

- Affected code: new `app/modules/api_keys/last_used_coalescer.py` (coalescer + flush scheduler + module singleton), `app/modules/api_keys/service.py` (settlement + `record_usage` record instead of UPDATE), `app/modules/api_keys/repository.py` (drop the per-request `update_last_used` write and the inline write in `increment_limit_usage`), `app/main.py` (lifespan start/stop wiring).
- Affected tests: new unit suite for the coalescer/flusher (coalescing, latest-wins, greatest-wins no-regression, shutdown flush); existing `test_api_keys_service.py` settlement/last-used assertions updated to the coalescer contract.
- Write-load effect: per-settlement `api_keys` row write is removed; steady-state `last_used_at` writes drop from one-per-request to at most one-per-active-key every 30 s.
- Staleness effect: dashboard `lastUsedAt` may lag real usage by up to ~30 s (plus flush commit). No functional consumer exists (no ordering/routing/enforcement reads), so the impact is display-only.
- No API schema change, no migration, no frontend change, no new settings.
