# Context: retention-dashboard-settings

## Rationale

Retention is a per-deployment policy the operator may want to tighten or
relax while watching disk usage — a restart-requiring env var is the wrong
shape for it (PRINCIPLES.md P2). The dashboard runtime-settings channel
(`dashboard_settings` row + `SettingsCache` with cross-replica invalidation)
already carries every comparable policy, so retention moves there.

## Precedence design

`NULL` in the dashboard column means "never set from the dashboard", which
keeps existing env-configured deployments working unchanged through the
deprecation window:

1. dashboard value (non-NULL, including `0` = explicitly disabled)
2. env alias (`CODEX_LB_REQUEST_LOG_RETENTION_DAYS` /
   `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`), deprecated
3. disabled (`0`)

The dashboard API mirrors the env safety floors exactly (0 or >= 30 request
logs / 0 or >= 45 usage history, max 3650) with the same error wording, so
in-product consumer windows stay inside retained data regardless of which
layer configured retention.

The GET settings API returns both the *effective* value per window
(`requestLogRetentionDays`, dashboard override falling back to the env
alias — same convention as the `proxy_account_*` concurrency caps) and the
raw nullable *override* (`requestLogRetentionOverrideDays`, `null` =
inherit). Updates use only the override fields, tri-state: absent =
unchanged, present `null` = clear back to inherit, present value = store —
including a value equal to the env alias, which deliberately pins it as a
dashboard override (a codex review P2 showed a value-based echo guard made
that capture impossible). Full-save clients echo the override fields
verbatim, so `null` round-trips as `null` and no echo heuristic is needed;
the dashboard card additionally submits only the fields the operator edited
and clears an override by emptying the input.

## Scheduler behavior

Previously the scheduler computed `enabled` once at startup from env
settings and did not start at all when disabled. It now always starts and
re-resolves the effective retention at the top of each hourly tick (a single
SettingsCache-backed read); when the effective configuration is disabled the
tick returns before leader election. Leader-election gating of the actual
pass (`run_if_leader`, heartbeat-renewed) is unchanged.

## Deprecation plan

- This release: env vars keep working as aliases; their comment in
  `app/core/config/settings.py` marks them deprecated. They are NOT added to
  `_REMOVED_SETTINGS`.
- A later phase removes the env fields (adding them to `_REMOVED_SETTINGS`
  with a pointer to the dashboard setting) once operators have had a release
  to migrate.

## Example

An operator running with `CODEX_LB_REQUEST_LOG_RETENTION_DAYS=90` opens
Settings -> Advanced -> Data retention, sees 90 prefilled (effective value),
and changes it to 30. The row now stores 30; within one scheduler tick the
leader prunes request logs older than 30 days — no restart, and the stale
env var no longer matters.

## Screenshots caveat

Screenshots under `screenshots/` are captured with the frontend preview
server and Playwright route interception (no live backend), matching the
`dashboard-progressive-disclosure` capture approach — the throwaway capture
spec is not committed. Sections whose endpoints are not mocked (quota phase
planner, sticky sessions) show their "Failed to fetch" banners; that is a
capture-harness artifact, not part of this change. The "before" shot is the
same build with the new card removed from the DOM (the card is this change's
only visual delta).
