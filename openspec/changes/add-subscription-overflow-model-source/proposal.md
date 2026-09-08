## Why

When every eligible subscription account has exhausted its usage limit, codex-lb can only answer `429 usage_limit_reached`. Issue #2123 designs an operator-designated model source that absorbs that overflow at its own cost while subscription routing stays untouched. The design lands in stages; this change carries the schema stage first so the migration sits alone on the Alembic graph and the behavioural stages can build on a settled data shape without re-parenting.

## What Changes

- Add two nullable `dashboard_settings` columns: `subscription_overflow_source_id` (the designated source; no foreign key, a dangling id means "off" like `single_account_id`) and `subscription_overflow_drain_until` (the deadline armed when a designation is cleared so pinned conversations can drain).
- Add the `model_source_pins` table (`pin_key` primary key, `kind`, `source_id` without a foreign key, nullable `api_key_id`, timezone-aware `created_at` / `last_seen_at` / `expires_at` / `purge_at`) with the `ix_model_source_pins_purge_at` index.
- One Alembic revision, `20260908_000000_add_subscription_overflow`, on the current head: idempotent against a partially applied schema, fully downgradable, identical logical schema on SQLite and PostgreSQL.
- **Designation settings (WP-B, inert).** `PUT /api/settings` accepts a tri-state `subscription_overflow_source_id` (absent = unchanged, `null` = off, value = designate) that only accepts an OpenAI-compatible source with Responses support (else `400 subscription_overflow_source_invalid`); clearing it arms the read-only `subscription_overflow_drain_until` deadline (`now + 29 d`), designating clears the deadline, and every write invalidates the settings cache across replicas. Deleting the designated model source clears the designation and arms the deadline in the same transaction as the delete.
- **Preflight (WP-B).** `GET /api/settings/subscription-overflow/preflight?source_id=` reports, for a source, whether it can be designated and what an operator should fix first: served vs missing registry models, models that can never overflow (Responses-Lite / code mode), undeclared Codex tool types, vision, streaming, pricing, context-window mismatches, source-scoped API keys, and live/tombstone pin counts. Only the kind/Responses checks block. The dashboard's Routing card gains the designation select, the inline preflight, and the help text; `docs/routing.md` gains the operator explainer.
- **No request-path behaviour change.** Nothing on the request path reads the designation, the drain deadline, or the pin table: the hot path's single `SettingsCache.get()` loads the same `dashboard_settings` row it already loaded and no code reads the new columns. An exhausted pool still answers `429 usage_limit_reached` with a source designated, and the source is never contacted. The pin repository and overflow routing arrive in later work packages (WP-C1/WP-C2) that extend this change and relax the inertness ratchet test deliberately.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-migrations`: the dashboard-settings and pin schema for subscription-exhaustion overflow is represented by ORM metadata and a single-head Alembic revision.
- `model-source-routing`: operators designate a subscription-overflow model source through the settings API and dashboard (tri-state, eligibility-validated, drain deadline, cleared by source deletion) and read a non-blocking preflight for it. Overflow routing itself is deliberately **not** specified here; it lands with WP-C2.

## Impact

- `app/db/models.py` (two `DashboardSettings` columns, the `ModelSourcePin` model), one new Alembic revision, migration tests (WP-A).
- `app/modules/settings/*` (schemas, service, repository, API, the new dashboard-only `subscription_overflow.py`), `app/modules/model_sources/api.py` (delete hook), the dashboard Routing card (`frontend/src/features/settings/*`, i18n en/ko/zh-CN), `docs/routing.md` (WP-B).
- New dashboard setting `subscription_overflow_source_id`: defaults off and cannot be a default because it names an operator-owned paid source. No `CODEX_LB_*` environment variable, README section, or navigation item.
- No proxy, CLI, or configuration surface changes; `app/modules/proxy`, `app/core`, and `app/modules/api_keys` are untouched and a unit ratchet keeps them free of the designation until WP-C2.
