# Database

SQLite is the default database backend and needs no configuration. PostgreSQL and MySQL are optional via `CODEX_LB_DATABASE_URL` (for example `postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb` or `mysql+asyncmy://codex_lb:codex_lb@127.0.0.1:3306/codex_lb`).

## Data paths

| Environment | Path |
|-------------|------|
| Local / uvx | `~/.codex-lb/` |
| Docker | `/var/lib/codex-lb/` |

Backup this directory to preserve your data (database, encryption key, archives).

## PostgreSQL via Docker Compose

The Docker Compose `postgres` profile uses the Postgres 18 image and mounts the named data volume at
`/var/lib/postgresql`, the parent of the image's versioned `PGDATA` directory. The `postgres` and
`postgres-upgrade` profiles live in the root
[`docker-compose.yml`](https://github.com/Soju06/codex-lb/blob/main/docker-compose.yml)
(`docker-compose.prod.yml` only defines the `server` service, for external PostgreSQL).

## Upgrading Postgres 16 → 18

Existing Postgres 16 compose volumes must be upgraded before the Postgres 18 container starts:

```bash
docker compose --profile postgres stop postgres
docker run --rm -v codex-lb-postgres-data:/var/lib/postgresql -v "$PWD:/backup" alpine \
  tar -C /var/lib/postgresql -czf /backup/codex-lb-postgres-data-before-pg18.tgz .
docker compose --profile postgres-upgrade run --rm postgres-upgrade
docker compose --profile postgres up -d postgres
```

The `postgres-upgrade` profile runs `pg_upgrade` in one-shot mode against the same named volume and exits after the
data directory has been upgraded to the Postgres 18 layout. Because that helper mounts and rewrites the operator's
database volume, Compose pins the helper image by digest; refresh and review the digest deliberately when changing the
helper image tag. Keep the backup until the application has started and `codex-lb-db check` succeeds against the
upgraded database.

The normal `postgres` service refuses to start when it detects the old root-level `PG_VERSION` file from a pre-18
Compose volume. If that guard fires, run the `postgres-upgrade` profile above before starting Postgres again.
It also refuses nested `/var/lib/postgresql/data` directories that still report a pre-18 major version, because those
layouts need an explicit pg_upgrade before the Postgres 18 container can safely open them.

## MySQL

MySQL is optional via `CODEX_LB_DATABASE_URL` (for example
`mysql+asyncmy://codex_lb:codex_lb@127.0.0.1:3306/codex_lb`). The application connects with `asyncmy`; Alembic
migrations run the same URL over `pymysql`. MySQL 8.0.13+ with InnoDB and `utf8mb4` is required (CI and the
reference rehearsal run `mysql:8.4`).

The schema is emitted for MySQL:

- unbounded string columns become sized `VARCHAR`s derived from production maxima, so every index key stays
  inside MySQL's 3072-byte `utf8mb4` limit;
- long text (model-registry snapshots, error traces) uses `MEDIUMTEXT` (16 MiB);
- text columns that carry keys (response ids, password hashes, sticky-session keys) are promoted to sized
  `VARCHAR`s;
- literal defaults on text columns are emitted in MySQL's expression form (`DEFAULT ('x')`), which MySQL
  accepts where the plain literal form is rejected;
- statements that read their own target table (batched retention deletes, the account-deletion drain, and the
  quota-planner claim) are rewritten through materialised derived tables to stay clear of MySQL error 1093.
- datetime columns are emitted as `DATETIME(6)` (with `DEFAULT CURRENT_TIMESTAMP(6)` where the models declare a
  server-side default), because MySQL's plain `DATETIME` rounds Python-supplied microseconds away -- limit
  windows, rollup watermarks and lease deadlines are compared against Python-computed instants, so MySQL has to
  round-trip them the way SQLite and PostgreSQL do;
- floating-point columns are emitted as `DOUBLE`: MySQL's bare `FLOAT` is 4-byte single precision, while the
  aggregates compared against raw recomputations (cost/token sums) assume 8-byte precision;
- rollup dimension columns that carry the `U+001F` sentinel encoding use a binary collation, because MySQL's
  default `utf8mb4_0900_ai_ci` treats the sentinel as ignorable and would fold `''` into `NULL`;
- bucket arithmetic floors the division explicitly, since MySQL's signed `CAST` rounds where SQLite truncates
  and PostgreSQL floors;
- an account marked for deletion records its `delete_history` variant in its own conditional statement before
  the marker update: MySQL evaluates a multi-column `UPDATE`'s `SET` list against the row as it stands, so a
  `CASE` reading a column the same statement writes would take the wrong branch;
- same-email account merges and same-identity reauth upserts serialize on exclusive `runtime_sentinels` row
  locks keyed by the same derivations PostgreSQL uses for its advisory locks
  (`advisory_lock_key('merge-email', email)`, `account_identity_lock_key(chatgpt_account_id)`, and the
  deterministic account id), acquired in sorted order: MySQL has no transaction-scoped advisory lock, and
  two concurrent imports could otherwise both insert (two rows for one email with merging enabled, or two
  rows for one upstream identity under reauth, which promises one).

The cross-process migration lock uses MySQL's own named lock (`GET_LOCK`/`RELEASE_LOCK`) on a dedicated
connection, mirroring the PostgreSQL advisory lock. Named locks are **server-wide, not per-database**: two
`upgrade` runs for different databases on one server still serialize against each other, which is exactly the
intent for a schema mutex and worth knowing when running several test databases side by side.

`docker compose --profile mysql up -d mysql` starts a local MySQL 8.4 with the `codex_lb` database.

### Sizing the server

Size InnoDB deliberately. A profile for a database of up to ~5 GiB:

```ini
[mysqld]
innodb_buffer_pool_size = 6G
innodb_buffer_pool_instances = 6
innodb_redo_log_capacity = 1G
innodb_flush_log_at_trx_commit = 1
max_connections = 200
tmp_table_size = 256M
max_heap_table_size = 256M
slow_query_log = 1
long_query_time = 0.5
```

Run the buffer pool at roughly 1.2-1.5x the working set on a dedicated server; keep
`innodb_flush_log_at_trx_commit = 1` for crash safety.

### Testing against MySQL

```bash
MYSQL_TEST_DATABASE_URL='mysql+asyncmy://codex_lb:codex_lb@127.0.0.1:3306/codex_lb' make test-mysql
MYSQL_TEST_DATABASE_URL='mysql+asyncmy://codex_lb:codex_lb@127.0.0.1:3306/codex_lb' make migration-check-mysql
```

The matching CI jobs (`test-mysql`, `migration-check-mysql`) run against a `mysql:8.4` service.

`MYSQL_PYTEST_TARGETS` covers the portable PostgreSQL list plus the MySQL-clean integration files beyond it
(99 targets, including the MySQL named-lock cases), so the MySQL job covers every integration that can run
there rather than a curated subset. Tests that genuinely require PostgreSQL semantics (advisory-lock
interleavings, query-plan assertions, `reloptions` tuning) are skipped with that reason in their summary
line.

---

*Specs: [database-backends](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/database-backends) · [database-migrations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/database-migrations)*
