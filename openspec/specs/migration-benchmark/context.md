# Measure a PostgreSQL migration range

This developer command produces advisory measurements for [#1471](https://github.com/Soju06/codex-lb/issues/1471). Its contract is in the [migration-benchmark spec](spec.md). It does not select a duration threshold, reference runner, CI trigger or maintenance-window policy.

## Run a measurement

Use a source checkout with `uv sync --frozen`. Provision an empty PostgreSQL database that you own and can discard. Keep other writers off it. The command rejects existing tables and never resets a database.

Set both database variables to the same dedicated disposable target before importing application code or running tests. Use an explicit `postgresql+asyncpg://` URL without query options. `--db-url-env NAME` reads the URL from that explicitly selected environment variable and pins both routes inside the command and its migration subprocesses. The variable must be nonempty. Provision it through a secret manager or protected shell input; do not put credentials in command arguments or shell history.

```bash
export CODEX_LB_DATABASE_URL="$disposable_database_url"
export CODEX_LB_TEST_DATABASE_URL="$CODEX_LB_DATABASE_URL"
uv run python -m scripts.benchmark_migrations \
  --db-url-env CODEX_LB_DATABASE_URL --disposable \
  --base 20260720_000000_add_request_log_conversation_id \
  --target 20260910_000000_request_logs_missing_cost_index \
  --rows 100000 --accounts 100 --seed 7 \
  --output /tmp/migration-measurement \
  --runner-note 'Describe database host, CPU and memory limits'
```

Base and target are exact Alembic IDs from the checked-out source, not Git revisions or aliases such as `head`. The base must be an ancestor of the target. The command upgrades an empty database to the base, seeds it, then measures each missing ancestor in Alembic order. The initial base upgrade is reported separately from the measured range. Equal base and target produce a no-op range with a seeded fixture.

Use a new output directory for each attempt. `report.json` contains machine-readable results; `report.md` includes the timing table and provenance. A clean committed checkout gives the strongest source identity. Dirty runs also report a diff digest and untracked-file digest.

## Interpret the result

Each measured revision uses a fresh `python -m app.db.migrate upgrade REVISION` process. Wall time includes Python startup, migration inspection, lock acquisition and the upgrade itself. Each step has a separate transaction scope and releases its migration lock. Earlier steps remain committed if a later step fails. Revision-defined Alembic autocommit blocks still apply. Summing these timings does not reproduce one production upgrade's transaction, downtime or concurrent load.

`status: completed` means the selected range finished. Durations never determine exit status. At the current source head, completion also requires the existing public migration `check` to pass. Historical targets still run that check, but its comparison to current ORM metadata can report expected drift. That check result is recorded separately and the command does not silently upgrade beyond the requested target.

Setup, seeding or upgrade failures produce nonzero exits and `status: failed`. The report records the stage, attempted revision, elapsed time and observed stamps when available. Each measured step retains its own `resulting_revisions`, including failed attempts. The top-level `resulting_revisions` records the final observed database state. Raw subprocess errors are omitted because they can contain credentials or SQL values. The report provides benchmark-owned error text or the exception class. Inspect the owned disposable database separately if more diagnosis is needed. An interrupted attempt remains incomplete; discard its database before a fresh run.

## Fixture limits

`modulo-v1` uses fixed timestamps and arithmetic on row number plus seed. Account IDs, emails and token blobs are synthetic and are not usable credentials. Accounts and three model names cycle uniformly. Every tenth row is an error; transports alternate. When the historical schema contains the columns, user agents cycle through two slash-containing names, an unknown string and null; half the costs are missing and every fifth service tier is priority. The report lists inserted columns and observed counts, including backfill-eligible user agents and missing costs.

The fixture populates only accounts and request logs. Other tables are empty. It is not a production workload model, and older bases without a column cannot exercise that column's populated-data path. For example, seeding immediately before the July user-agent backfill exercises it; seeding the February schema leaves user agents absent until later migrations add the column.

The seed transaction runs `ANALYZE` before measurement. Hardware, container limits, PostgreSQL settings and warmed caches affect results. Record database-host details through `--runner-note`; automatic CPU/platform fields describe the Python runner, which may be on another host.

## Verify the command

Run the subprocess contract tests explicitly with both variables pointing at an owned PostgreSQL test database. Provision a PostgreSQL superuser on the disposable test server for the full suite. `CREATEDB` alone is insufficient: the failure-injection test uses `CREATE EVENT TRIGGER`, which [requires a superuser](https://www.postgresql.org/docs/18/sql-createeventtrigger.html). That test verifies the active role before creating its trigger. Tests create and drop uniquely named child databases, including one with an intentional DDL failure.

The benchmark command itself does not require a superuser.

```bash
uv run pytest tests/integration/test_migration_benchmark.py -q
```

The command is not wired into CI or application startup. Delete only the database/container and files you created for the experiment when finished. Keep its reports with the source revision under review.
