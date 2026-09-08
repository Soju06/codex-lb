## Why

The Codex HTTP-bridge prewarm canary experiment retired in
`reduce-settings-surface-phase-4` (issue #1340 phase 4, shipped in v1.22.0).
Its `request_logs.prewarm_canary_bucket` / `prewarm_eligible_reason` columns
were deliberately left declared-but-unwritten for one release so replicas
still running the previous version could keep inserting request logs while
the Helm pre-upgrade migration job ran ahead of the workload roll. Three
releases (1.22, 1.23, 1.24) have since shipped without a writer, so the
queued Alembic drop revision is overdue and the ORM still carries two dead
attributes.

## What Changes

- Remove `prewarm_canary_bucket` and `prewarm_eligible_reason` from the
  `RequestLog` ORM model.
- Add Alembic revision `20260908_000000_drop_prewarm_canary_columns` on top of
  `20260830_000000_add_quota_warmup_claim_expiry` that drops both columns via
  `batch_alter_table` (SQLite table recreation, plain `ALTER TABLE` on
  PostgreSQL). Upgrade and downgrade are idempotent against a schema where the
  columns are already absent / present; downgrade re-adds them nullable.
- Close item 1 of the next-release queue in
  `openspec/specs/deployment-installation/context.md`.

## Capabilities

### Modified Capabilities

- `proxy-runtime-observability`: the request log no longer carries the legacy
  canary bucket / eligibility cohort columns at all.

## Impact

`app/db/models.py`, a new Alembic revision, the migration integration suite,
and the observability / deployment context notes. No runtime code path reads
or writes the columns; dashboards never exposed them. Operators rolling back
to a pre-1.25 image after this migration must run the downgrade first (the
standard "migration deployed, old image cannot restart" caveat already in the
release runbook).
