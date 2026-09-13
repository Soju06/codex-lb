# Tasks

## 1. Registry

- [x] 1.1 `SETTING_TIERS`: `proxy_unauthenticated_client_cidrs`, `dashboard_trust_loopback_host_header_for_long_sessions`, `proxy_response_create_limit` → T1 with a one-line topology reason; delete their `MIGRATING` rows; `# T1 (topology)` comments on the fields in `Settings`.
- [x] 1.2 `DASHBOARD_HOMES` registry with `telemetry_enabled → dashboard_settings.telemetry_consent`; delete the `telemetry_enabled` `MIGRATING` row.

## 2. Check

- [x] 2.1 `scripts/check_settings_tiers.py`: accept a `DASHBOARD_HOMES` mapping as a T3 database home; error on a malformed target or a non-existent column (verified against `Base.metadata`); warn on redundant entries and on a field listed in both registries.
- [x] 2.2 Unit tests for each new error/warning path and a live-tree assertion that `DASHBOARD_HOMES` and `MIGRATING` are disjoint.

## 3. Documentation

- [x] 3.1 `docs/configuration.md`: telemetry variable is a fallback until a dashboard decision exists; remaining moves tracked in `MIGRATING`.
- [x] 3.2 `docs/reference/settings.md` regenerated.
- [x] 3.3 `openspec validate retier-topology-settings --strict`, `openspec validate --specs --strict`, `make lint`, `uv run pytest tests/unit/test_settings_tiers.py tests/unit/test_settings_reference.py`.
