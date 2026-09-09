# Change: dashboard-managed-context-window-overrides

## Why

`CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` (`slug -> reported context window`) was environment-only: raising or reverting a model's advertised window meant editing the environment on every replica and restarting, while a too-large window turns every request for that slug into a pre-stream 400 until the next restart. `configuration-tiers` classifies that as a T3 defect — a value an operator changes while the proxy is running belongs in the dashboard, with the fixed precedence environment < dashboard. The field was listed in the `MIGRATING` backlog (M4 of the slop-removal campaign, plan §3). The value is a mapping, so the dashboard home is a small override table rather than a `dashboard_settings` column.

## What Changes

- New table `model_context_window_overrides` (`slug` PK, `context_window`, timestamps), migration `20260909_110000_model_context_window_overrides`. The migration and first boot never copy the environment dict into rows.
- Per-slug precedence: a dashboard row wins for its slug, a slug without a row inherits the environment entry, a slug with neither has no override. `app/core/config/context_window_overrides.py` is the single resolver (`resolve_context_window_overrides`) and holds the `ModelContextWindowOverridesCache` (TTL + the cross-replica `settings` invalidation namespace, like `SettingsCache`).
- Settings sub-API under `/api/settings/model-context-window-overrides`: `GET` (merged list with per-slug provenance `source` / `env_value`), `PUT /{slug}` (create or replace), `DELETE /{slug}` (404 without a row). Slug and window validation as specified.
- The catalog endpoints resolve the merged overrides once per build from the cached snapshot (outside the per-model loops) and thread them into the entry builders; `_resolved_context_window` keeps the `max_context_window` clamp.
- Dashboard: new "Model catalogue" card in the Advanced group — slug/window rows with add, edit and remove, a "clamped to the upstream `max_context_window`" hint, and a per-row provenance badge; environment-inherited rows are read-only until overridden.
- `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` stays as a deprecated per-slug fallback for one release (`T3 (dashboard)` in the settings reference); its `MIGRATING` row is replaced by a `DASHBOARD_HOMES` mapping to the new table.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-catalog-compat`: new requirement "Per-model context window overrides are dashboard settings"; the three context-window requirements and the GPT-5.6 bootstrap scenarios name the dashboard row as the override source with the environment entry as fallback.

## Impact

- Migration `20260909_110000_model_context_window_overrides` (sqlite and PostgreSQL; downgrade drops the table).
- Code: `app/core/config/context_window_overrides.py` (new), `app/modules/proxy/api.py` catalog builders, settings ORM/repository/schemas/API, `tiers.py`, `main.py` poller wiring; frontend settings API, hook, "Model catalogue" card, three locales.
- API: additive settings sub-resource; `GET /v1/models` and `GET /backend-api/codex/models` are unchanged on the wire.
- Operators: overrides are editable live; the environment variable keeps working until the next minor release.
