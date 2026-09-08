# Tasks

## 1. Contract (this change)

- [x] 1.1 `specs/configuration-tiers/spec.md`: tier definitions with the replica question, fixed precedence, fallback-not-seed, single resolver + snapshot consumers, API provenance shape, mandatory tier declaration in `SETTING_TIERS`, settings-field budget, T3 database home or `MIGRATING` entry (target column or `backlog`), `os.environ` confinement with a shrink-only allowlist, T0/T1-only operator docs, one-release deprecation and immediate deletion of unread fields.
- [x] 1.2 `specs/proxy-admission-control/spec.md`: MODIFIED "Dashboard-configurable account concurrency caps" — first-row creation leaves cap overrides NULL; scenario for an environment change after first boot.
- [x] 1.3 `context.md`: the six combination patterns, the fourteen silo findings, and the post-#1340 regrowth as rationale.
- [x] 1.4 `PRINCIPLES.md` P6 + "Applying these principles" row; `.github/CONTRIBUTING.md` simplicity gate 6; `.github/PULL_REQUEST_TEMPLATE.md` tier line; `docs/configuration.md` "Where settings live" paragraph linking the spec.
- [x] 1.5 `openspec validate --specs`, `openspec validate codify-configuration-tiers`, `make lint`, simplicity budgets check.

## 2. Machine enforcement (slop-removal B4 PR, change `enforce-configuration-tiers`)

- [ ] 2.1 `app/core/config/tiers.py`: `SETTING_TIERS` covering every `Settings` field and `MIGRATING` for every T3 field without a same-name `dashboard_settings` column (value = target home or `backlog`); `scripts/check_settings_tiers.py` fails on a missing or unknown tier, a T3 field with neither a column nor a `MIGRATING` entry, and `.env.example` entries whose tier is not T0/T1; stale tier/`MIGRATING` entries warn.
- [ ] 2.2 `os.environ` / `os.getenv` / `dotenv_values` under `app/` outside `app/core/config/settings.py` fails `make lint` except for files in `ENV_READ_ALLOWLIST`, each capped at its current number of reading lines (over the cap fails, under it warns). The `get_settings().<T3 field>` consumer rule stays reviewer-enforced until a follow-up adds an architecture rule for it.
- [ ] 2.3 `scripts/generate_settings_reference.py` emits the Tier column and legend from `SETTING_TIERS`; `[settings_fields].max` in `.github/simplicity-budgets.toml`, read by the check and by `tests/unit/test_settings_reference.py`.
- [ ] 2.4 Run `check_settings_tiers.py` from `make lint` (`architecture-check`), which the CI `lint` job already runs.

## 3. Data-model alignment (slop-removal B5 PR)

- [ ] 3.1 `SettingsRepository.get_or_create` stops copying environment values into `proxy_account_response_create_limit`, `proxy_account_stream_limit`, `proxy_account_stream_recovery_reserve`, `proxy_api_key_fair_share_congestion_threshold_pct`; existing rows are preserved (equality with the current environment value cannot prove a value was seeded rather than set by an operator) and the release notes tell operators on existing installs to clear a cap in the dashboard to return to inheritance.
- [ ] 3.2 `telemetry_enabled`: `resolve_consent` treats the environment value as the fallback for an undecided (NULL) consent decision — `source: "env"` still suppresses the consent dialog — and a persisted dashboard decision wins over it; the environment value is never written into the consent row.
- [ ] 3.3 `upstream_stream_transport`: remove the `default` sentinel and the environment field; Helm `configmap.yaml` and `docs/client-setup.md` updated.
- [ ] 3.4 `GET /api/settings` exposes `{value, source, env_value, default}` per T3 setting; `PUT` accepts `null` to clear; flat `*_environment_value` / `*_override` fields kept for one release; dashboard shows an "inherited from env/default" badge when `source != "dashboard"`.
- [ ] 3.5 Spec deltas that B5 MUST carry so the archived specs do not contradict `configuration-tiers`: MODIFIED `telemetry` "Settings toggle and environment kill switch" (environment is the fallback for an undecided consent state, never a seed; a persisted dashboard decision wins) and MODIFIED `rate-limit-reset-credits` "Reset credit polling interval is configurable" (`rate_limit_reset_credits_refresh_enabled` gets a dashboard home or the AND-gate on `auto_redeem_reset_credits_before_expiry` is removed).
- [ ] 3.6 Seed-once NOT NULL columns (`http_downstream_transport_policy`, `openai_cache_affinity_max_age_seconds`, `warmup_model`): delete the environment fields (their only reader is the first-row seed), add the names to `_REMOVED_SETTINGS`, remove `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS` from Helm `configmap.yaml`; `proxy-warmup` "Warmup model defaults on first settings creation" stays true via the code default.

## 4. Environment-read confinement (slop-removal B6 PR)

- [ ] 4.1 Promote the out-of-`Settings` reads (`CODEX_LB_CONNECT_ADDRESS`, `CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE`, `FORWARDED_ALLOW_IPS`, `TZ`, and the rest of inventory Table 1a) to `Settings` fields with `SETTING_TIERS` entries; lower or delete the corresponding `ENV_READ_ALLOWLIST` caps and raise `[settings_fields].max` by the number of promoted fields in the same diff.
- [ ] 4.2 Regenerate `docs/reference/settings.md`; `.env.example` and `docs/configuration.md` list T0/T1 only.
