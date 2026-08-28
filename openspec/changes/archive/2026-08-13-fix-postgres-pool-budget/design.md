## Context

A PostgreSQL replica creates one request-path engine at import time and a separate background engine during startup. Both engines use the configured `database_pool_size` and `database_max_overflow`, so the worst-case per-replica capacity is twice the configured per-engine capacity. Helm currently documents and sizes only one pool.

## Goals / Non-Goals

**Goals:**

- Derive the engine-count budget from the declared PostgreSQL engine roles used by the real creation paths.
- Keep the owned server launcher on the supported one-worker-per-replica topology even when `WEB_CONCURRENCY` is present.
- Use the complete two-engine topology in Helm capacity guidance and policy tests.
- Keep default and production HPA ceilings at 80 application connections, leaving 20 raw server slots for PostgreSQL reservations, migrations, and operations.

**Non-Goals:**

- Changing which workloads use the background engine.
- Reintroducing independently configurable background-pool settings.
- Adding runtime discovery of PostgreSQL `max_connections`, a pooler, or a new setting.

## Decisions

- Keep both engines identically sized. The background pool was intentionally isolated for burst traffic, and splitting one configured pool asymmetrically would silently change the established setting semantics.
- Declare request-path and background-task PostgreSQL engine roles and require both creation paths to use a role-validating factory. Derive the per-worker engine count from those roles, so adding a declared role changes the budget automatically.
- Explicitly pass `workers=1` to Uvicorn in `app.cli`. The project supports one worker per replica and horizontal scaling through replicas; pinning the owned launcher prevents Uvicorn from interpreting `WEB_CONCURRENCY` as an implicit multi-worker request without attempting worker auto-detection.
- Size chart defaults at `(3 + 1) * 2 * 10 = 80`. Keep the production overlay at `(1 + 1) * 2 * 20 = 80`. A 20-slot raw reserve covers PostgreSQL's default three superuser-reserved slots, the migration path's two-connection peak (advisory-lock holder plus operation connection), and fifteen further operational connections.
- Test the budget from parsed Helm values against the role-derived runtime count, exercise both real engine creation paths, and verify that the owned CLI pins one worker even when `WEB_CONCURRENCY` is set.

## Risks / Trade-offs

- [Smaller pools can increase checkout waits during spikes] → Preserve three steady plus one overflow connection per default-chart engine, one steady plus one overflow per production engine, and retain the existing 30-second checkout timeout; operators with a larger server budget can tune upward using the documented formula.
- [A future pooled engine could invalidate Helm math] → Require every PostgreSQL application engine to declare a role through the shared factory and derive policy-test engine count from the role enum.
- [A custom launcher can still create multiple workers] → Keep custom multi-worker Uvicorn/Gunicorn topologies explicitly unsupported; only the owned `app.cli` launcher and Helm workload are pinned and budgeted.
