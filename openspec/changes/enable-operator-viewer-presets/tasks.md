## 1. Admin-level definition and TOTP policy

- [x] 1.1 `dashboard_access.py`: `is_admin_level(grants)` (any `PRIVILEGED_PERMISSIONS` entry) and `totp_policy_applies(required_on_login, required_for_admin_role, grants)`.
- [x] 1.2 `dashboard_settings.totp_required_for_admin_role` (model + Alembic `20260910_000000_add_totp_required_for_admin_role`, server default false, downgrade drops the column).
- [x] 1.3 `validate_dashboard_session` and the dashboard-auth service (management session, TOTP-verified session, login-success audit, `describe_session`) use the shared predicate; `requires_auth` keeps following the global toggle only.

## 2. Settings contract

- [x] 2.1 `totp_required_for_admin_role` in the settings response/request schemas, `SECURITY_SETTINGS_FIELDS`, the merge in `PUT /api/settings` and its `settings_changed` changed-field list.
- [x] 2.2 Enable guard on the transition of either toggle reads the acting account's TOTP secret (`400 invalid_totp_config`); `totpConfigured` means the acting account has TOTP; `usersWithoutTotpCount` / `adminsWithoutTotpCount` computed from active password accounts.
- [x] 2.3 `SettingsRepository.list_active_password_users()`; `_to_data` replaces the duplicated row mapping.

## 3. Dashboard gating

- [x] 3.1 Conversations view, request-log sensitive metadata and archive panel follow `conversations:read`; account actions `accounts:write`; API-key page and request-log key filter `api_keys:read`; Export `accounts:export`.
- [x] 3.2 Security controls follow `security:write`: guest access, session length, "require TOTP on login" (`TotpSettings.canEditPolicy`), API-key auth/quota-privacy toggles (`ApiKeysSection.policyControlsDisabled`), firewall (disabled), proxy endpoint creation (`UpstreamProxySettings.canCreateEndpoint`); upstream-proxy administration `ops:write`.
- [x] 3.3 People tab: **Require two-factor for administrators** toggle (with `security:write`), enrolment hint from `adminsWithoutTotpCount`, inline wording for `invalid_totp_config`; `VIEWER_PERMISSIONS` factory; en/ko/zh-CN strings.

## 4. Verification

- [x] 4.1 Unit: `is_admin_level`, `totp_policy_applies`.
- [x] 4.2 Integration (`tests/integration/test_operator_viewer_presets.py`): Operator and Viewer through invite → accept → sign in against the route matrix; admin-role policy binds admin preset and a custom role with `audit:read` but not Operator; enable guard 400 without own secret; counts; audit changed field; migration up/down/idempotent.
- [x] 4.3 Existing suites adjusted for the new column (`SimpleNamespace`/fake settings, migration head assertion).
- [x] 4.4 Frontend: operator/viewer Conversations gating, request-detail metadata for operators, nav for viewer/operator, Settings security controls read-only for an operator, Access card personal-only controls without `security:write`, Export gating, People tab toggle (render/hide, save, guard, other refusals).
- [x] 4.5 `uv run ruff check . && uv run ruff format --check . && uv run ty check`; pytest on the auth/settings/users/matrix/gates/conversations/audit/e2e sets; `bun run lint`, `bun run typecheck`, full `bun run test`; `openspec validate enable-operator-viewer-presets --strict`; simplicity budgets unchanged.

## 5. Documentation

- [x] 5.1 `docs/authentication.md`: what Operator and Viewer can do now that they are assignable; the administrator two-factor requirement and its enable guard.
