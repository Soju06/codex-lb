# Change: settings-provenance

## Why

Six dashboard settings inherit a fallback when their column is NULL — the four per-account capacity caps (`proxy_account_response_create_limit`, `proxy_account_stream_limit`, `proxy_account_stream_recovery_reserve`, `proxy_api_key_fair_share_congestion_threshold_pct`) fall back to the process environment and then the code default, and the two retention windows fall back to "disabled". The settings API exposes them as three flat fields each (`<name>`, `<name>_environment_value`, `<name>_override`) and the dashboard shows an "Inherited effective value" hint, so an operator cannot tell whether a number is the code default, an environment variable somebody set on this deployment, or a value saved in the dashboard — the question `configuration-tiers` requires the API to answer. Each cap also had its own `_effective_<name>()` helper re-stating the same precedence, and the retention helper a third variant, while the settings row was mapped to the service data class by two identical seventy-line blocks. The environment-to-dashboard migration planned in `configuration-tiers` (`MIGRATING`, 65 fields) needs one resolver and one provenance surface to plug new settings into, before the first group of timeouts moves.

## What Changes

- `SettingsService` resolves every inheritable setting through one generic resolver, `resolve_inheritable(column_value, env_value, default)`, returning the effective value together with its source (`dashboard` when the column is non-NULL, `env` when the column is NULL and the environment value differs from the code default, `default` otherwise). The six per-setting effective-value helpers are removed and the duplicated row-to-data mapping collapses into one function.
- `GET /api/settings` and the `PUT /api/settings` response gain an additive `provenance` map keyed by setting name with `{source, env_value, default}` for every inheritable setting (the four caps and the two retention windows, which report no environment value). Every existing field, including the flat `<name>_environment_value` / `<name>_override` fields, is unchanged.
- `PUT /api/settings` tri-state semantics for inheritable fields are documented on the request schema: omitted = unchanged, `null` = clear the dashboard value and return to inheritance, value = set. No behaviour change; the `clear_*` flags already implement this.
- Dashboard: a shared `InheritBadge` component (with the `useInheritableSetting` hook) shows "Inherited from environment (value)" or "Default (value)" for a setting the dashboard does not own and a "Reset to inherited" action, which sends an explicit `null`, for a setting it does. It replaces the effective-value hint under the four capacity inputs and falls back to that hint against a backend that reports no `provenance`. Strings added in all three locales, with a key-parity test.
- No migration, no new setting, no new column.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `configuration-tiers` (introduced by the pending `codify-configuration-tiers` change): ADDED requirement fixing the concrete API shape for provenance — the `provenance` map alongside the existing effective-value field — and the dashboard's inherited/reset affordance. It realises the "settings API reports value, source, environment value and default" requirement of that change: `value` stays the flat effective field, `source` / `env_value` / `default` live in `provenance[<name>]`. Nullable database-only columns (retention) report `source: "dashboard"` whenever non-NULL, including an explicit `0`, and `source: "default"` with no `env_value` when NULL.
- `proxy-admission-control`: ADDED requirement — the settings API reports the provenance of each account concurrency cap and the routing settings show it. The existing "Dashboard-configurable account concurrency caps" requirement is not modified here (it is already amended by `codify-configuration-tiers`).

## Impact

- Code: `app/modules/settings/service.py` (resolver, `InheritableValue`, single mapper), `app/modules/settings/schemas.py` (`SettingProvenance`, `provenance`, request docstring), `app/modules/settings/api.py` (response mapping); frontend `features/settings/schemas.ts`, `components/inherit-badge.tsx`, `hooks/use-inheritable-setting.ts`, `components/routing-settings.tsx`, three locale files.
- API: additive field on `GET`/`PUT /api/settings` responses only. Older dashboards ignore it; the new dashboard tolerates its absence.
- Operators: the capacity section now says where each cap comes from and offers a one-click return to inheritance.

Part of the slop-removal campaign 0908 (C2-0, the foundation for the env-to-dashboard migration groups C2-1..C2-3).
