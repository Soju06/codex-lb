## Why

When every eligible subscription account has exhausted its usage limit, codex-lb can only answer `429 usage_limit_reached`. Issue #2123 designs an operator-designated model source that absorbs that overflow at its own cost while subscription routing stays untouched. The design lands in stages; this change carries the schema stage first so the migration sits alone on the Alembic graph and the behavioural stages can build on a settled data shape without re-parenting.

## What Changes

- Add two nullable `dashboard_settings` columns: `subscription_overflow_source_id` (the designated source; no foreign key, a dangling id means "off" like `single_account_id`) and `subscription_overflow_drain_until` (the deadline armed when a designation is cleared so pinned conversations can drain).
- Add the `model_source_pins` table (`pin_key` primary key, `kind`, `source_id` without a foreign key, nullable `api_key_id`, timezone-aware `created_at` / `last_seen_at` / `expires_at` / `purge_at`) with the `ix_model_source_pins_purge_at` index.
- One Alembic revision, `20260908_000000_add_subscription_overflow`, on the current head: idempotent against a partially applied schema, fully downgradable, identical logical schema on SQLite and PostgreSQL.
- **No behaviour change.** Nothing reads or writes the new columns or table. The settings designation API and dashboard control, the preflight endpoint, the pin repository, and overflow routing arrive in later work packages that extend this change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: the dashboard-settings and pin schema for subscription-exhaustion overflow is represented by ORM metadata and a single-head Alembic revision.

## Impact

- `app/db/models.py` (two `DashboardSettings` columns, the `ModelSourcePin` model), one new Alembic revision, migration tests.
- No API, dashboard, proxy, CLI, configuration, or documentation surface changes in this stage.
