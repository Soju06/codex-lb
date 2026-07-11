## Tasks

- [x] Add Alembic migration `20260711_000000_add_limit_warmup_idle_threshold`
      adding `limit_warmup_idle_threshold_percent` column (Float, default 1.0).
- [x] Add `limit_warmup_idle_threshold_percent` to `DashboardSettings` model
      in `app/db/models.py`.
- [x] Add the new field to settings service dataclasses and mappings
      (`app/modules/settings/service.py`).
- [x] Add the new field to settings schemas
      (`app/modules/settings/schemas.py`).
- [x] Add the new field to settings repository defaults and update logic
      (`app/modules/settings/repository.py`).
- [x] Add the new field to settings API response, update handler, and audit
      list (`app/modules/settings/api.py`).
- [x] Wire `limit_warmup_idle_threshold_percent` into the staggered idle call
      site in `app/modules/limit_warmup/service.py`.
- [x] Add `limitWarmupIdleThresholdPercent` to frontend schemas
      (`frontend/src/features/settings/schemas.ts`).
- [x] Add the new field to frontend payload
      (`frontend/src/features/settings/payload.ts`).
- [x] Add "Idle at %" input to routing-settings component, shown beneath the
      staggered idle toggle when enabled
      (`frontend/src/features/settings/components/routing-settings.tsx`).
- [x] Add the new field to settings-page dependency array
      (`frontend/src/features/settings/components/settings-page.tsx`).
- [x] Update backend tests: `_settings()` helper, staggered idle tests,
      settings API tests, migration tests, audit changed-fields tests.
- [x] Update frontend test fixtures: schemas.test.ts, payload.test.ts,
      routing-settings.test.tsx, factories.ts.
- [x] Update OpenSpec change folder: proposal, spec delta, migration spec
      delta, tasks.
- [x] Run `openspec validate fix-staggered-idle-warmup-upstream-floor --strict`.
- [x] Run `uv run ruff check` and `uv run ruff format --check`.
- [x] Run `uv run pytest tests/unit/test_limit_warmup.py tests/integration/test_settings_api.py tests/integration/test_settings_audit_changed_fields.py tests/integration/test_migrations.py`.
- [x] Run frontend typecheck.
