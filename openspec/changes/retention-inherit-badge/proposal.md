# Change: retention-inherit-badge

## Why

`settings-provenance` (#2214) gave every inheritable setting a `provenance` entry and a shared dashboard affordance (`InheritBadge` / `useInheritableSetting`) that the account-capacity caps, the resilience toggles, the upstream timeouts and the routing/overload knobs now use. The Data retention card was left on its bespoke "Not configured: effective {{value}} days" hint under a temporary allowance in `configuration-tiers` ("the two retention windows MAY keep their effective-value hint until their form is migrated"). The backend already resolves `request_log_retention_days` and `usage_history_retention_days` through the same resolver and reports `source` `default` | `dashboard` for them (no environment fallback since #2190), so the only thing missing is the form. Two different visual languages for the same concept on one page is exactly the kind of divergence `configuration-tiers` exists to remove.

## What Changes

- The Data retention card renders the shared `InheritBadge` under each window: `Default (0)` while the column is NULL (not configured = retention disabled) and `Reset to inherited` while the dashboard owns the value. The reset sends the existing tri-state `PUT /api/settings` with an explicit `null` for that window only.
- The bespoke hint and its `settings.retention.inheritedHint` string are removed from `en`, `ko` and `zh-CN`. The tri-state input semantics (empty = clear to `null`, `0` = disabled, 30/45-day floors) and their validation messages are unchanged.
- `configuration-tiers`: the temporary allowance for the retention windows is removed; every inheritable setting the dashboard exposes uses the shared affordance, and a plain hint is permitted only when the backend response carries no `provenance`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `configuration-tiers`: MODIFIED "The settings API reports value, source, environment value and default" (retention windows join the shared affordance; the migration allowance is deleted; one scenario added).

## Impact

- Frontend only: `frontend/src/features/settings/components/data-retention-settings.tsx`, its test, three locale files.
- No API, schema or backend change; no new setting; no default changes.

Part of the slop-removal campaign 0908 (follow-up to #2214 / #2219).
