# Change: codify-configuration-tiers

## Why

codex-lb keeps its configuration in two stores with no rule about which value belongs where. The environment store (`CODEX_LB_*`, 135 `Settings` fields) is restart-only — `get_settings()` is an `lru_cache`, so zero environment values can be hot-reloaded — yet 113 of those fields are read on the request or scheduler-tick path. The dashboard store (`dashboard_settings`, 55 data columns) is live, shared by every replica, and visible on screen. Twenty-one settings exist in both stores and are combined by six different ad-hoc rules (nullable override, seed-once-then-dead, sentinel value, env-wins kill switch, env AND-gates a dashboard toggle, mixed runtime config objects). The result is fourteen catalogued places where one store silently shadows the other: environment values that stop working after first boot but are still templated by Helm, dashboard toggles that cannot be enabled without editing an environment file, and a first-boot seed that copies environment values into the dashboard row as explicit overrides so the "inherits environment value" label in the UI is untrue on every fresh install. The July settings-surface reduction (#1340) had no rule to hold the line, and the surface has since regrown to 135 fields (the campaign inventory counts 120 → 138 including non-`Settings` env reads). Evidence: `context.md`.

## What Changes

- Add a new capability `configuration-tiers` that assigns every setting to one of five tiers — T0 bootstrap, T1 instance topology, T2 secret, T3 behaviour tunable, T4 incident debug — using one discriminating question ("may the value legitimately differ between two replicas?"), and fixes the combination rule to a single precedence: code default < environment < dashboard, where the environment never overrides a non-NULL dashboard value and is never copied into the dashboard row as a seed.
- Require a single resolver (`SettingsService` effective-value functions) and `SettingsCache` snapshot reads for T3 fields, a `value / source / env_value / default` shape per T3 setting in the settings API, a tier for every `Settings` field declared in the registry `app/core/config/tiers.py` (`SETTING_TIERS`), a database home for every T3 field or a `MIGRATING` entry naming its target column or `backlog`, a settings-field budget (`[settings_fields].max` in `.github/simplicity-budgets.toml`) that moves with the field it admits, no `os.environ` access outside `app/core/config/settings.py` except a shrink-only per-file allowlist for third-party/POSIX/uvicorn-launcher variables and sites awaiting promotion, T0/T1-only content in `.env.example` and `docs/configuration.md`, a Tier column in the generated settings reference, and a one-stable-release warning window (via the removed-settings registry) before an environment name is deleted — with immediate deletion for environment fields nobody reads.
- Amend `proxy-admission-control` "Dashboard-configurable account concurrency caps": a settings row created for the first time leaves the cap overrides NULL (inherit) instead of persisting the process environment values.
- Add `PRINCIPLES.md` P6 "The dashboard is the primary configuration surface", a matching CONTRIBUTING simplicity gate, a PR-template line asking for the tier of every new setting, and a "Where settings live" paragraph in `docs/configuration.md` linking back to the spec.
- This change is documentation and contract only. It defines the mechanism; the CI check that enforces it (`scripts/check_settings_tiers.py` under `make lint`, the tier registry, the field-count ratchet, the Tier column) lands in the sibling `enforce-configuration-tiers` change (slop-removal B4), whose delta adds the check requirements to this capability without redefining it; the first-boot seed removal (existing rows preserved), `telemetry_enabled` precedence flip and `upstream_stream_transport` sentinel removal in B5; promotion of out-of-`Settings` `os.environ` reads in B6.

## Capabilities

### New Capabilities

- `configuration-tiers`: the normative contract for where a setting lives (tier definitions and the replica question), how environment and dashboard values combine (fixed precedence, fallback-not-seed, single resolver, snapshot consumers), how the settings API reports provenance, and how environment settings are added, documented, deprecated and deleted.

### Modified Capabilities

- `proxy-admission-control`: first-row creation no longer persists process environment values for the per-account cap overrides; NULL inherits the environment (or code default) until an operator sets a value, so an environment change made after first boot takes effect.

Known conflicts not amended here: `telemetry` ("Settings toggle and environment kill switch", env overrides persisted consent) and `rate-limit-reset-credits` ("Reset credit polling interval is configurable", env toggle gates a dashboard opt-in) still mandate inversions of the new precedence. Their deltas belong to the B5 change that removes the inversions from code (tasks 3.5); this change is not archived before that.

## Impact

- Code: none in this change. Follow-up PRs implement the CI check, the data migration and the env-read promotions against this spec.
- Docs: `PRINCIPLES.md` (P6 + table row), `.github/CONTRIBUTING.md` (gate 6), `.github/PULL_REQUEST_TEMPLATE.md` (tier line), `docs/configuration.md` ("Where settings live").
- Specs: new `openspec/specs/configuration-tiers/spec.md` (via this delta); `proxy-admission-control` requirement amended.
- Operators: no behaviour change until B5 lands; after it, a fresh install that later changes `CODEX_LB_PROXY_ACCOUNT_*` sees the new value without clearing anything in the dashboard.

Part of the slop-removal campaign 0908 (batch B3).
