# Change: Replica-aware model registry

## Why

`ModelRegistry` is a process-local singleton and `registry.update()` runs only inside the leader's `ModelRefreshScheduler` tick. On PostgreSQL multi-replica with leader election enabled — the supposedly correct HA configuration — every non-leader replica serves the static bootstrap catalog for its whole lifetime: `/v1/models` is stale, plan gating silently degrades (`plan_types_for_model` falls back to bootstrap or returns None), and `is_suppressed_model` always returns False, so withdrawn models keep routing and plan-gated models reach accounts that upstream 4xxes, penalizing healthy accounts' error health. Persisting the leader-refreshed snapshot to the database and having every replica load it via the existing cache-invalidation bus makes the catalog replica-coherent while keeping exactly one upstream fetcher, and gives restarts a warm catalog before the first refresh.

## What Changes

- New single-row table `model_registry_snapshot` (Alembic migration) holding a versioned JSON serialization of the full registry state plus a `content_hash`, wall-clock `refreshed_at`, and diagnostic `leader_id`.
- New codec/store module `app/core/openai/model_registry_store.py`: field-complete export/import of `ModelRegistrySnapshot` + metadata models (including a `cleared` marker), atomic single-row upsert persist (PostgreSQL and SQLite `ON CONFLICT DO UPDATE`), full load, and a cheap header-only probe.
- `ModelRegistry.export_state()` / `import_state()` (lock-held), converting persisted wall-clock `refreshed_at` to/from the monotonic `fetched_at` so TTL semantics survive import.
- Leader path in `ModelRefreshScheduler._refresh_once`: after a successful `registry.update()` (or `clear()`), persist the snapshot (payload write skipped when `content_hash` is unchanged; `refreshed_at` still touched) then `bump(NAMESPACE_MODEL_REGISTRY)` — write-then-bump, mirroring api_key/firewall.
- Non-leader path: instead of returning early, reconcile from the persisted snapshot when the stored header differs from the applied one (TTL backstop at the refresh tick against lost bumps); never fetch upstream.
- `app/main.py`: register a `NAMESPACE_MODEL_REGISTRY` poller callback (load + `import_state` + local `AccountSelectionCache` invalidate) and load the persisted snapshot once during lifespan startup, gated by a new `model_registry_snapshot_max_age_seconds` staleness cap (default 86400).
- Spec deltas in `model-catalog-compat`; regression tests at the `/v1/models` route and gating surfaces using a two-registry/two-poller simulation over one database.
