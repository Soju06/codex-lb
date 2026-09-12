# Tasks

## 1. Backend

- [x] 1.1 Alembic revision adding the nullable `dashboard_settings.http_responses_session_bridge_codex_prewarm_enabled` column; ORM, repository seed (NULL) and tri-state update parameters.
- [x] 1.2 `SettingsService` resolves the switch through `resolve_inheritable`; `DashboardSettingsData`, `Response` and `UpdateRequest` carry it with provenance; API maps it and audits layer moves.
- [x] 1.3 The switch joins `DASHBOARD_OVERRIDE_SETTINGS` so the request-bound overlay folds the dashboard column over the env alias; `_http_bridge_prewarm_enabled(settings)` stays a one-argument memory read and `_maybe_prewarm_http_bridge_session` resolves it before `prewarm_lock`, adding no settings read under the lock.
- [x] 1.4 `MIGRATING` entry removed; env field annotated `T3 → dashboard (deprecated env alias, remove next minor)`.

## 2. Dashboard

- [x] 2.1 Schema fields, "Session bridge" card with the "Codex session prewarm" switch and `InheritBadge`; strings in `en`, `ko`, `zh-CN`.
- [x] 2.2 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 Unit: overlay states (NULL → env, NULL + env unset → default, dashboard value including `false`), prewarm path honours dashboard over env in both directions, and a spy over a full prewarm body proving the path adds no settings-cache read of its own and opens no database session.
- [x] 3.2 Integration: `PUT /api/settings` round trip (default → dashboard → cleared/env → unchanged on omit, column stays NULL), dashboard value reaches the bridge resolver without a restart, bridge request prewarms with dashboard on / env off and does not with dashboard off / env on, migration upgrade/downgrade.
- [x] 3.3 `make lint`, `uv run ty check`, `make migration-check`, frontend lint/typecheck/vitest, `openspec validate dashboard-managed-codex-prewarm --strict`, `docs/reference/settings.md` regenerated.
