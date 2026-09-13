## Why

Seven `CODEX_LB_*` env fields are documented as live configuration but are
dead or past their deprecation window. `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` /
`CODEX_LB_USAGE_HISTORY_RETENTION_DAYS` were deprecated one-release aliases
for the dashboard retention settings (`retention-dashboard-settings`, PR
#1364, issue #1340 next-release queue); v1.22, v1.23 and v1.24 have shipped
since. `CODEX_LB_HTTP_DOWNSTREAM_TRANSPORT_POLICY`,
`CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS` and `CODEX_LB_WARMUP_MODEL`
are only copied into the `dashboard_settings` row when it is first created,
so on every initialized deployment the env var is ignored while the docs and
the Helm chart present it as configuration.
`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_GATEWAY_SAFE_MODE` has zero readers
(only the dashboard column is read). `CODEX_LB_WORKERS_PER_INSTANCE` accepts
exactly one value — its default — so it is a guard, not a setting. Finally the
52 `_REMOVED_SETTINGS` names from the July 2026 phases 1-4 have had their
one-release startup warning (PRINCIPLES.md P2: a setting the operator never
needs to touch is a default in disguise).

## What Changes

- **BREAKING** (documented env vars disappear; `extra="ignore"` means no
  deployment fails to start): remove the seven env fields above from
  `Settings`. Startup keeps rejecting `CODEX_LB_WORKERS_PER_INSTANCE` values
  other than `1` with the same error, now as an environment guard rather than
  a field.
- Retention precedence loses its env layer: a non-NULL dashboard value wins,
  NULL (never configured) is disabled. Operators who still rely on the env
  alias get the one-release startup warning naming the variable and must set
  the window from Settings -> Advanced -> Data retention.
- First-boot seeding of `http_downstream_transport_policy`,
  `openai_cache_affinity_max_age_seconds` and `warmup_model` uses the column
  defaults (`smart`, `1800`, `gpt-5.4-mini`) — identical to the env defaults.
- Rotate `_REMOVED_SETTINGS`: drop the 52 phase 1-4 names (their names stay
  inert through `extra="ignore"`, just without the warning) and add the six
  names removed here so they get their one-release warning. The mechanism
  (`warn_removed_settings`) is unchanged.
- Generated settings reference: no more "deprecated aliases" list, a
  startup-guard note for `CODEX_LB_WORKERS_PER_INSTANCE`, ratchet 137 -> 130 (rebased onto #2187, which had promoted three env reads to fields).
- Helm chart stops rendering `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS`
  (`config.cacheAffinityMaxAgeSeconds` removed) so a default install does not
  trip its own removal warning.

## Capabilities

### Modified Capabilities

- `deployment-installation`: removed-settings warning covers the current
  batch only; Helm renders no removed names.
- `data-retention`: dashboard value or disabled — no env alias layer.
- `proxy-admission-control`: `CODEX_LB_WORKERS_PER_INSTANCE` is a startup
  guard on the environment, not a `Settings` field.
- `user-documentation`: the generated reference lists removed names only;
  ratchet lowered.

## Impact

`app/core/config/settings.py`, `app/modules/settings/{service,repository}.py`,
`app/core/retention/job.py`, `app/modules/proxy/_service/streaming/retry.py`,
`scripts/generate_settings_reference.py`, `docs/reference/settings.md`,
`docs/deployment/kubernetes.md`, Helm chart configmap/values, dashboard
retention hint copy, and the corresponding unit/integration tests. No schema
change, no API change (`requestLogRetentionDays` still reports the effective
value, `*OverrideDays` still round-trips).
