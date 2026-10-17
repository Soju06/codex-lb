# Tasks

## 1. Backend

- [x] 1.1 Alembic revision creating `model_context_window_overrides`; ORM model; `ModelContextWindowOverridesRepository` (list, upsert, delete).
- [x] 1.2 `app/core/config/context_window_overrides.py`: per-slug resolver, effective-map helper, `ModelContextWindowOverridesCache` invalidated through the `settings` namespace; poller callback registered in `main.py`.
- [x] 1.3 Settings sub-API `GET`/`PUT /{slug}`/`DELETE /{slug}` with slug and window validation and provenance per slug.
- [x] 1.4 Catalog builders resolve the merged overrides once per build and thread them into `_to_model_list_item` / `_to_codex_model_entry`; `_resolved_context_window` keeps the clamp.
- [x] 1.5 `MIGRATING` row replaced by a `DASHBOARD_HOMES` mapping; env field annotated `T3 → dashboard (deprecated env alias, remove next minor)`; settings reference regenerated.

## 2. Dashboard

- [x] 2.1 Schemas, API client, `useModelContextWindowOverrides` hook, "Model catalogue" card with add/edit/remove, clamp hint and per-row provenance badge; strings in `en`, `ko`, `zh-CN`; MSW handlers.
- [x] 2.2 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 Unit: resolver states (dashboard over env, env-only, dashboard-only, per-slug merge).
- [x] 3.2 Integration: sub-API round trip with provenance, 404 on delete without a row, invalid window (422) and slug (400) rejection, slash in slug; the four catalog override tests seed dashboard rows; dashboard value ≠ environment applied by the catalog without restart; clamp retained; migration upgrade/downgrade.
- [x] 3.3 `make lint`, `uv run ty check`, `make migration-check`, frontend lint/typecheck/vitest, `openspec validate dashboard-managed-context-window-overrides --strict`, `docs/reference/settings.md` regenerated.
