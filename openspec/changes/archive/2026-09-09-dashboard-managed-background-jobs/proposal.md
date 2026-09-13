# Change: dashboard-managed-background-jobs

## Why

Three background loops — the Auth Guardian (proactive credential refresh of idle accounts), the automations scheduler (dispatch of scheduled automation runs) and reset-credit polling (per-account rate-limit reset credits and automatic redemption) — could only be switched off through `CODEX_LB_*` environment variables, so pausing one during an incident meant a rollout and a restart of every replica. `configuration-tiers` classifies the three switches T3 ("the dashboard is the management surface") and lists them in `MIGRATING`. Worse, the switches gated only `start()`: even a dashboard column would have been a lie because a value changed at runtime would not have reached a loop that was never started (or could not stop one that was).

## What Changes

- Three nullable `dashboard_settings` BOOLEAN columns of the same names (alembic `20260909_090000_dashboard_background_job_toggles`): `auth_guardian_enabled`, `automations_scheduler_enabled`, `rate_limit_reset_credits_refresh_enabled`. NULL means "inherit": the environment variable, then the code default (`true`). Nothing is seeded from the environment (`configuration-tiers` P-D). Default behaviour is unchanged.
- `GET`/`PUT /api/settings` expose `authGuardianEnabled`, `automationsSchedulerEnabled`, `rateLimitResetCreditsRefreshEnabled` (effective values) plus `provenance.<name>` through the shared `resolve_inheritable` resolver, accept the tri-state update (omitted = unchanged, `null` = back to inherited, boolean = dashboard value), and additionally report `authGuardianBlockedByTopology` so the UI can say why the guardian is idle. The `CODEX_LB_*` fields stay one release as deprecated aliases (`# T3 → dashboard (deprecated env alias, remove next minor)`) and leave `MIGRATING`.
- **Toggles take effect on the next tick without a restart.** Every loop now always starts, with the whole tick (the settings read included) inside the loop's exception guard so a transient database error cannot kill the task; each cycle reads the `SettingsCache` snapshot at its entry — the guardian's refresh pass, the automations tick (`AutomationsScheduler._run_due_once`, `AutomationsService.run_due_jobs`) and the reset-credit refresh cycle — and skips while the effective value is `false`. No database read happens inside a scheduler lock or per-item loop. `AutomationsService.run_now()` refuses (`409 automations_paused`) while paused so the "paused" label is never a lie.
- The Auth Guardian keeps its topology gate: a multi-replica ring without leader election blocks every pass whatever the toggle says (the dashboard cannot override it); the gate is exposed as `authGuardianBlockedByTopology` and shown next to the switch.
- The reset-credit auto-redeem gate on `PUT /api/settings` reads the effective (dashboard-aware) polling toggle, including the value the same request sets or clears, instead of the environment variable alone, and becomes symmetric: now that polling is a dashboard value, the reverse request (disable polling while the opt-in is on) could otherwise create the same unrunnable pair, so it is refused with the same error code. Its message points at the dashboard card. The startup configuration-conflict warning likewise uses the effective value.
- `telemetry/snapshot.py` derives `features.automations` from the effective toggle.
- Dashboard: a "Background jobs" switch group under Settings → Advanced with the shared `InheritBadge` (the guardian row shows the topology note), plus a "Pause all automations" switch in the Automations page header wired to the same setting (the per-job Run now action is disabled while paused). Strings in `en`, `ko`, `zh-CN`.
- `docs/reference/settings.md` regenerated (the three settings are `T3 (dashboard)`); `docs/configuration.md` names the new group and drops the "one variable still gates a dashboard value" exception.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: MODIFIED "Proactive active account credential refresh" (the dashboard setting, with the env variable as deprecated fallback, decides per pass; a change applies on the next pass without a restart) and MODIFIED "Multi-replica leader guard" (a dashboard `true` does not override the topology gate; the API reports the block).
- `automations`: ADDED "Automation scheduling can be paused from the dashboard" (paused ticks dispatch nothing, `run-now` is refused with a conflict, resume applies on the next tick).
- `rate-limit-reset-credits`: MODIFIED "Reset credit polling can be disabled" (dashboard-managed toggle read per cycle; the auto-redeem gate and the startup warning use the effective value). This delta is written against the text `constantize-core-tunables` adds; archive that change first.
- `configuration-tiers`: unchanged as a delta (this change realises its `MIGRATING` procedure for three fields). The pending `retier-topology-settings` delta's example, which named `rate_limit_reset_credits_refresh_enabled → fold into …`, is reworded in place because that field now has its own column.

## Impact

- Schema: three nullable columns on `dashboard_settings` (SQLite and PostgreSQL, guarded add/drop).
- API: additive fields on `GET`/`PUT /api/settings`; `POST /api/automations/{id}/run-now` gains a `409 automations_paused` outcome; older dashboards ignore the new fields, the new dashboard tolerates their absence.
- Code: `app/core/config/background_jobs.py` (new resolver + topology helper), `app/core/auth/guardian.py`, `app/modules/automations/{scheduler,service,api}.py`, `app/core/usage/reset_credits_refresh_scheduler.py`, `app/modules/telemetry/snapshot.py`, settings module (`models`, `repository`, `service`, `schemas`, `api`), `app/core/config/{settings,tiers}.py`, frontend `features/settings` (schemas, `background-jobs-settings.tsx`, page) and `features/automations` (`automations-pause-toggle.tsx`, `use-automations-paused.ts`, page), three locales.
- Operators: no action. `CODEX_LB_AUTH_GUARDIAN_ENABLED`, `CODEX_LB_AUTOMATIONS_SCHEDULER_ENABLED` and `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_ENABLED` keep working until the dashboard value is set; the reference page says so. The env fields are removed in the next minor via `_REMOVED_SETTINGS`.

Part of the slop-removal campaign 0908 (migrating-triage M2).
