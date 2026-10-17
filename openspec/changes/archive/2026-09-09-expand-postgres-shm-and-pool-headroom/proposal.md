# Expand PostgreSQL /dev/shm and default pool headroom

## Why

Two independent capacity ceilings surfaced on a production single-replica
PostgreSQL deployment:

- The Compose `postgres` service runs with Docker's default 64MB `/dev/shm`.
  PostgreSQL parallel workers (`work_mem=32MB`,
  `max_parallel_workers_per_gather=2`) exchange spill files through dynamic
  shared memory under `/dev/shm`, so parallel hash joins abort with
  `could not resize shared memory segment ... No space left on device`,
  which asyncpg surfaces as `DiskFullError` on the request path.
- The default SQLAlchemy pool (`database_pool_size=15`,
  `database_max_overflow=10`, fixed 30s checkout timeout) exhausts under
  slow-query pile-ups: once 25 request-path checkouts are held, every further
  request waits 30 seconds and fails with
  `QueuePool limit of size 15 overflow 10 reached, connection timed out`.

## What Changes

- The Compose `postgres` service sets `shm_size: 1gb`.
- Default `database_pool_size` rises 15 → 25 and `database_max_overflow`
  10 → 15, keeping the per-replica two-engine cap at
  `(25 + 15) * 2 = 80` application connections — inside PostgreSQL's default
  `max_connections=100` with at least 20 raw server slots reserved (same
  reserve rule the Helm capacity guidance already mandates).
- Helm deployments are unaffected: the chart always injects its own
  `CODEX_LB_DATABASE_POOL_SIZE` / `CODEX_LB_DATABASE_MAX_OVERFLOW` values,
  and the bundled Bitnami PostgreSQL sub-chart already mounts a
  memory-backed `/dev/shm` (`shmVolume.enabled=true` by default).

## Capabilities

### Modified Capabilities

- `deployment-installation`: the Compose Postgres profile provisions a
  `/dev/shm` large enough for parallel query.
- `database-backends`: default pool sizing preserves the raw-slot reserve on
  PostgreSQL's default `max_connections`.

## Impact

- SQLite deployments: none (pool sizing applies to pooled backends only).
- Helm deployments: none (chart values override both settings).
- Compose/manual PostgreSQL deployments: applying `shm_size` requires the
  postgres container to be recreated (seconds of downtime); per-replica
  worst-case application connections rise from 50 to 80.
