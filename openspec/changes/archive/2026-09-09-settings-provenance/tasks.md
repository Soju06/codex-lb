# Tasks

## 1. Backend

- [x] 1.1 `resolve_inheritable()` and `InheritableValue` in `app/modules/settings/service.py`; the four cap helpers and the retention helper removed; one `_settings_data(row)` mapper replaces the two duplicated constructor blocks; `DashboardSettingsData.provenance`.
- [x] 1.2 `SettingProvenance` schema and additive `DashboardSettingsResponse.provenance`; tri-state PUT semantics documented on `DashboardSettingsUpdateRequest`; `_dashboard_settings_response` maps the provenance.
- [x] 1.3 Unit tests: NULL + env ≠ default → `env`; NULL + env == default → `default`; database-only NULL → `default` without env; value (including 0) → `dashboard`; service populates every inheritable setting. Integration: one cap round-trips through default → dashboard → env → unchanged-on-omit, retention reports no environment value.

## 2. Dashboard

- [x] 2.1 `SettingProvenanceSchema` + optional `provenance` on the settings response schema.
- [x] 2.2 `InheritBadge` + `useInheritableSetting`; wired to the four capacity inputs; legacy hint fallback (only under an empty input) when `provenance` is absent; reset disabled with a reason when clearing would put the reserve above the stream limit.
- [x] 2.3 `settings.inherit.*` strings in `en`, `ko`, `zh-CN`; locale key-parity test.
- [x] 2.4 Before/after screenshots of the capacity section (light and dark) in the PR.

## 3. Verification

- [x] 3.1 `make lint`, `uv run ty check`, `make migration-check` (no migration), settings unit + integration tests, frontend lint/typecheck/vitest, simplicity budgets, `scripts/check_settings_tiers.py`, `openspec validate --specs` and `openspec validate settings-provenance --strict`.
