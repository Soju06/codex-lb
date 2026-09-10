# Tasks

## 1. Backend

- [x] 1.1 Alembic `20260909_090000_dashboard_background_job_toggles`: nullable BOOLEAN `auth_guardian_enabled`, `automations_scheduler_enabled`, `rate_limit_reset_credits_refresh_enabled` on `dashboard_settings`; ORM columns; repository seeds NULL and applies tri-state updates.
- [x] 1.2 `DashboardSettingsData` / `Response` / `UpdateRequest` carry the effective booleans and `auth_guardian_blocked_by_topology`; provenance entries via `resolve_inheritable`; audit records ownership-only changes.
- [x] 1.3 `Settings` fields kept as deprecated aliases with the `# T3 → dashboard (deprecated env alias, remove next minor)` marker; `tiers.MIGRATING` entries removed; `check_settings_tiers` passes.
- [x] 1.4 `app/core/config/background_jobs.py`: `resolve_background_job_toggle(snapshot, name)`, `background_job_enabled(name)` (one `SettingsCache` read per cycle), `auth_guardian_blocked_by_topology(settings)`.
- [x] 1.5 Schedulers always `start()`; the guardian pass, the automations tick / `run_due_jobs` / `run_now()` and the reset-credit cycle read the snapshot at entry and skip while `false`; `run_now()` refuses with `409 automations_paused`; the guardian keeps the topology gate (`topology_blocked`).
- [x] 1.6 Settings API auto-redeem gate and the scheduler's startup warning read the effective polling toggle; `telemetry/snapshot.py` reads the effective automations toggle.

## 2. Dashboard

- [x] 2.1 `authGuardianEnabled` / `authGuardianBlockedByTopology` / `automationsSchedulerEnabled` / `rateLimitResetCreditsRefreshEnabled` on the settings schema (nullable on the update request); factories updated.
- [x] 2.2 `BackgroundJobsSettings` switch group in Advanced settings with `InheritBadge` per switch and the topology note; `AutomationsPauseToggle` in the Automations page header (Run now disabled while paused); strings in `en`, `ko`, `zh-CN`.
- [x] 2.3 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 Unit: resolver precedence for the three names, settings-cache read, topology matrix (single / multi+election / multi−election), guardian and reset-credit tick sequences (enabled → dashboard off → skipped → on → runs) with a fake clock, automations tick sequence, builders always start, service provenance, telemetry effective value. Integration: API round trip (default → dashboard → cleared/env → unchanged on omit, column stays NULL, topology flag), relaxed auto-redeem gate, each scheduler honours a dashboard value that differs from the environment without a restart, paused `run-now` → 409 and `run_due_jobs` → 0.
- [x] 3.2 `make lint`, `uv run ty check`, `make migration-check`, frontend lint/typecheck/vitest, simplicity budgets, `openspec validate dashboard-managed-background-jobs --strict`, archive simulated on a copy of main's `openspec/` (after `constantize-core-tunables`), `docs/reference/settings.md` regenerated.
