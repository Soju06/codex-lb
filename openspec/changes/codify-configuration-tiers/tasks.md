# Tasks

## 1. Contract (this change)

- [x] 1.1 `specs/configuration-tiers/spec.md`: tier definitions with the replica question, fixed precedence, fallback-not-seed, single resolver + snapshot consumers, API provenance shape, mandatory tier declaration, T3 database home or `migrating_until`, `os.environ` confinement, T0/T1-only operator docs, one-release deprecation and immediate deletion of unread fields.
- [x] 1.2 `specs/proxy-admission-control/spec.md`: MODIFIED "Dashboard-configurable account concurrency caps" — first-row creation leaves cap overrides NULL; scenario for an environment change after first boot.
- [x] 1.3 `context.md`: the six combination patterns, the fourteen silo findings, and the post-#1340 regrowth as rationale.
- [x] 1.4 `PRINCIPLES.md` P6 + "Applying these principles" row; `.github/CONTRIBUTING.md` simplicity gate 6; `.github/PULL_REQUEST_TEMPLATE.md` tier line; `docs/configuration.md` "Where settings live" paragraph linking the spec.
- [x] 1.5 `openspec validate --specs`, `openspec validate codify-configuration-tiers`, `make lint`, simplicity budgets check.

## 2. Machine enforcement (slop-removal B4 PR)

- [ ] 2.1 `json_schema_extra={"tier": ...}` on every `Settings` field; `scripts/check_settings_tiers.py` fails on a missing tier, a T3 env field without a `dashboard_settings` home or `migrating_until` marker, an expired `migrating_until`, an env field with zero readers under `app/`, and `.env.example` entries whose tier is not T0/T1.
- [ ] 2.2 Architecture lint rule: `get_settings().<T3 field>` outside `app/modules/settings/service.py` fails `make lint`; `os.environ` / `os.getenv` / `dotenv_values` outside `app/core/config/settings.py` fails.
- [ ] 2.3 `scripts/generate_settings_reference.py` emits the tier column; `settings_fields` ratchet in `.github/simplicity-budgets.toml`.
- [ ] 2.4 Wire `check_settings_tiers.py` into CI and cite it from CONTRIBUTING gate 6.

## 3. Data-model alignment (slop-removal B5 PR)

- [ ] 3.1 `SettingsRepository.get_or_create` stops copying environment values into `proxy_account_response_create_limit`, `proxy_account_stream_limit`, `proxy_account_stream_recovery_reserve`, `proxy_api_key_fair_share_congestion_threshold_pct`; alembic migration sets existing rows to NULL where the stored value equals the current environment value.
- [ ] 3.2 `telemetry_enabled`: dashboard decision wins over a non-NULL dashboard value; environment remains the pre-first-boot opt-out seed only.
- [ ] 3.3 `upstream_stream_transport`: remove the `default` sentinel and the environment field; Helm `configmap.yaml` and `docs/client-setup.md` updated.
- [ ] 3.4 `GET /api/settings` exposes `{value, source, env_value, default}` per T3 setting; `PUT` accepts `null` to clear; flat `*_environment_value` / `*_override` fields kept for one release; dashboard shows an "inherited from env/default" badge when `source != "dashboard"`.

## 4. Environment-read confinement (slop-removal B6 PR)

- [ ] 4.1 Promote the out-of-`Settings` reads (`CODEX_LB_CONNECT_ADDRESS`, `CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE`, `FORWARDED_ALLOW_IPS`, `TZ`, and the rest of inventory Table 1a) to `Settings` fields with declared tiers.
- [ ] 4.2 Regenerate `docs/reference/settings.md`; `.env.example` and `docs/configuration.md` list T0/T1 only.
