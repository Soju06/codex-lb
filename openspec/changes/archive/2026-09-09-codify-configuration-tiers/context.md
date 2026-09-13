# Context: codify-configuration-tiers

## Purpose

An operator who wants to change a timeout, a cap or a feature toggle should not have to read code to find out whether the value lives in an environment variable (restart, per-replica, invisible) or in the dashboard (live, shared, visible). Today the answer depends on the history of the individual setting. This change writes down the rule so that reviewers and CI can hold it: the dashboard is the primary configuration surface; the environment is for what must exist before the database is reachable and for what legitimately differs between replicas.

Evidence: slop-removal campaign 0908, `04-config-inventory.md` and `05-config-policy.md` (local, `~/work/codex-lb/slop-removal-0908/`), measured against `main` @ `70f888d55`.

## Snapshot (2026-09-08)

| Metric | Count |
|--------|-------|
| Env-backed `Settings` fields | 135 (`MAX_SETTINGS_FIELDS` ratchet in `tests/unit/test_settings_reference.py`) |
| Removed env names still WARNed at startup (`_REMOVED_SETTINGS`) | 52 |
| Env fields read on the request/tick path but frozen at boot (`get_settings()` is `lru_cache`) | 113 |
| Env fields that can be hot-reloaded | 0 |
| Boot-only env fields | 22 |
| Settings present in both env and `dashboard_settings` | 21 |
| Distinct env↔DB combination rules in use | 6 |
| Env-only behaviour tunables / feature flags (no dashboard home) | 42 / 16 |
| Env fields editable from the dashboard | 10 (7%) |
| `os.environ` / `os.getenv` reads outside `app/core/config/settings.py` | 14 named variables (Table 1a) |
| Silo defects catalogued | 14 (S1–S14) |

The July settings-surface reduction (#1340, 164 → ~114 fields) came with a test ratchet but no placement rule. The ratchet has since been raised field by field to 135; the campaign inventory counts the total env surface as 120 → 138 (+21 since the reduction, mostly HTTP-bridge internals). Every increment was individually justified under P2; collectively they show that "justify the knob" without "say where the knob lives" does not stop regrowth.

## The six combination patterns found in the code

1. **Nullable override, NULL inherits env** (`proxy_account_*` caps, fair-share threshold, two retention days). The only pattern with a written rule — but the first-boot seed in `SettingsRepository.get_or_create` copies the env values into the cap columns as non-NULL, so the "inherit" state never exists on a fresh install and later env edits are silently ignored while the UI still labels the (new) env value as inherited. The `proxy-admission-control` sentence "A settings row created for the first time MUST persist the process environment values" mandated this; this change amends it.
2. **NOT NULL column seeded once from env, env dead afterwards** (`http_downstream_transport_policy`, `openai_cache_affinity_max_age_seconds`, `warmup_model`). Runtime readers only look at the row; Helm still templates the env var as live config.
3. **Sentinel in DB means inherit env** (`upstream_stream_transport = "default"`). Clear in code; Helm and `docs/client-setup.md` steer operators to the env var for a value the dashboard owns.
4. **Env wins over DB** (`telemetry_enabled`, `dashboard_bootstrap_token`). `PUT /api/settings/telemetry` returns 200 and persists a decision that has no effect.
5. **Env AND-gates a dashboard feature** (`rate_limit_reset_credits_refresh_enabled` gates `auto_redeem_reset_credits_before_expiry`; `quota_planner_scheduler_enabled` gates `quota_planner_settings.mode`; auth posture env fields gate `dashboard_session_ttl_seconds`). The dashboard error text tells the operator to edit an env file and restart.
6. **Mixed runtime config objects** (HTTP bridge idle TTLs: two env + one DB knob combined with `max()`). Lowering the dashboard TTL changes nothing if the env TTL is larger.

## The fourteen silo findings, summarised

- **Seed contradicts inherit** (S1): four cap columns seeded non-NULL from env at first boot.
- **Env dead after first boot but documented/templated as live** (S2 cache-affinity max age, S3 warmup model, S4 downstream transport policy) and **env never read at all** (S5 bridge gateway safe mode — only the DB column is consumed).
- **Dashboard write silently overridden by env** (S6 telemetry consent).
- **Dashboard toggle un-enableable without a restart** (S7 reset-credit auto-redeem; S8 quota planner has two off switches, one invisible in the UI).
- **Four independent sources decide client trust** (S9: DB allowlist, env CIDR bypass, env trusted-proxy CIDRs, uvicorn `FORWARDED_ALLOW_IPS`, the last undocumented).
- **Same knob family split across layers and merged with `max()`** (S10 bridge idle TTLs).
- **Sentinel plus docs/Helm that push env** (S11 upstream stream transport).
- **DB value silently clamped by env posture** (S12 dashboard session TTL > 30 d clamped to 12 h with no UI indication).
- **Two timezone sources for two schedulers** (S13 process `TZ` vs `quota_planner_settings.timezone`).
- **Ad-hoc env override read outside `Settings`** (S14 `CODEX_LB_CONNECT_ADDRESS`; invisible to the reference generator and the removed-settings WARN).

## Decisions

- **One discriminating question.** "May this value legitimately differ between two replicas?" Yes → environment (T1). No, and it is needed before the database is reachable → environment (T0). Otherwise → dashboard (T3). This is the whole tier test; the table in the spec is its expansion.
- **Fixed precedence, no inversions.** Code default < environment < dashboard, with the environment acting as a fallback for a NULL dashboard value only. An explicit environment "lock" for GitOps-style deployments (a `CODEX_LB_LOCKED_SETTINGS` allowlist rendered read-only in the UI) is left as an owner decision; implicit env-wins paths remain prohibited.
- **Fallback, not seed.** Copying env into the dashboard row makes the UI lie. First-row creation leaves T3 columns NULL.
- **Single resolver, snapshot consumers.** Effective values are computed in one place (`SettingsService` `_effective_<name>()`); T3 consumers read the `SettingsCache` snapshot, which is already invalidated across replicas through the `settings` cache-invalidation namespace. Leader election is not involved in settings propagation.
- **Provenance in the API.** `{value, source, env_value, default}` per T3 setting replaces the current per-setting triplet of flat fields (`x`, `x_environment_value`, `x_override`).
- **Deprecation through the existing registry.** `_REMOVED_SETTINGS` / `warn_removed_settings` already gives one stable release of startup WARN for removed names; migrations reuse it instead of inventing a second mechanism. Fields with zero readers skip the window — there is nobody to warn.
- **Spec first, enforcement next.** This change carries no code so that the contract can be reviewed on its own; the CI check, migration and env-read promotions land as separate PRs against it (tasks §2–§4).

## Open owner decisions (not resolved by this change)

- D1 explicit env lock for T3 values (GitOps/enterprise), tied to the RBAC plan.
- D2 tier of `DASHBOARD_AUTH_MODE` (proposed T1: reverse-proxy dependent; dashboard edit risks self-lockout).
- D3 leader election (proposed T1) vs scheduler on/off toggles (proposed T3).
- D4 telemetry: `TELEMETRY_ENABLED` as T3 with the environment as the fallback for an undecided consent state (no seed); `TELEMETRY_ENDPOINT` as T1.
- D5 migration order and release batching for the 42 tunables and 16 flags.

## Failure modes

- **Tier assigned but never enforced**: without the CI check the `SETTING_TIERS` registry is decoration. Mitigation: B4 (`enforce-configuration-tiers`) runs `check_settings_tiers.py` from `make lint` before any tier-driven cleanup is merged.
- **`MIGRATING` used as a permanent escape hatch**: the registry has no expiry, so it can only be held to "shrink only" by review. Mitigation: every entry names its target column or `backlog` so the remaining work is enumerable, redundant entries warn, and a new T3 field may only be added to `MIGRATING` when its PR names the follow-up that adds the column. The same applies to `ENV_READ_ALLOWLIST` caps, which the check lets fall but never rise silently.
- **Provenance shape drift**: if the API exposes `source` but the UI ignores it, S1-style lies return. Mitigation: the spec requires the badge; B5 owns it.
