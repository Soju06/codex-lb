# Database

SQLite is the default database backend and needs no configuration. PostgreSQL is optional via `CODEX_LB_DATABASE_URL` (for example `postgresql+asyncpg://codex_lb:codex_lb@127.0.0.1:5432/codex_lb`).

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

## Recovering an image rollback with an unknown revision

If startup reports a revision unknown to the image, first deploy a matching or newer image. An older image cannot infer compatibility from the revision label.

Stamping changes migration metadata only. It does not undo schema changes, restore data or cancel database transactions. Even additive changes can break old readers or writes through new constraints and defaults.

Use a metadata-only stamp for an image rollback only after these checks:

1. Stop application writers and migration jobs. Confirm their database transactions have ended, including server-side queries from an interrupted deployment. Stopping a container alone is not proof.
2. Preserve a consistent, recoverable backup of the current database, its encryption key and required data files. Record the current image, database target and recorded revisions. Do not replace newer writes with an old backup.
3. On a disposable copy, verify that the rollback image can read and write the existing schema and data. Review every intervening schema and data migration, including incomplete backfills. If compatibility is unknown or incompatible, keep the matching image or plan a reviewed downgrade using the required migration scripts.
4. Select the exact revision expected by the rollback image. Use a build containing both the recorded and target revisions. The old build cannot stamp an unknown current revision; do not delete or purge migration metadata to bypass that error.
5. Only after those checks, run `python -m app.db.migrate stamp <revision>` in that migration-capable build, replacing `<revision>` with the verified target. Keep the same explicit database configuration. Do not use the newer build's `head`, which can name a different revision.
6. Switch to the rollback image while writers remain stopped. Run `python -m app.db.migrate current` and `python -m app.db.migrate check` against the same database. Retain the encryption key, verify startup and the rehearsed read/write behavior, then resume traffic. A successful stamp or schema check alone does not prove application compatibility.

For example, a new release might add an optional column that the previous release ignores. Stamping back can be considered only after the previous release's reads and writes pass on a copy, including constraints and data semantics. A removed column or an unfinished backfill required by that release rules out this shortcut.

---

*Specs: [database-backends](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/database-backends) · [database-migrations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/database-migrations)*
