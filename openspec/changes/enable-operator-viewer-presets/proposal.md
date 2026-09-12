## Why

Phase 1 made Operator and Viewer assignable (`manage-dashboard-users-and-invites`) and the backend already separates their permissions, but the dashboard still decides what to show from the coarse `write` alias and the wire role `admin`: an Operator sees the Conversations view, the credential Export button, the guest-access, session and TOTP-requirement controls and the firewall form, and every one of them answers `403 permission_required`. Nothing proves end to end that an invited Operator or Viewer gets exactly what the role tables say. And with more than one administrator on an install, the only two-factor policy is "everyone with a password" — there is no way to require it of the people who can change security settings without also forcing it on every viewer (PLAN.md §4.4, D9, PR-2a).

## What Changes

- **Operator / Viewer separation, verified**: integration tests create an Operator and a Viewer through the product path (admin invites, accept, sign in) and assert the route matrix for those principals — Operator: operations succeed, `conversations:read`, `audit:read`, `accounts:export`, `security:write` and `users:manage` routes refuse; Viewer: the guest read surface (masked account identity), every mutation refused. No route gate needed correction; the tests pin the contract.
- **Permission-based gating in the dashboard**: every page and control follows the permission its backend route demands instead of `canWrite` / `role === "admin"` — Conversations view and request-log sensitive metadata/archive (`conversations:read`), account credential Export (`accounts:export`), account actions (`accounts:write`), the API-key page and filter (`api_keys:read`), guest access, session length, the "require TOTP on login" toggle, the API-key auth/quota-privacy toggles, firewall entries and proxy endpoint creation (`security:write`), upstream-proxy administration (`ops:write`). An Operator sees security controls read-only (never a dead-end 403); a Viewer sees the read-only surface a guest sees plus their own account menu. Navigation items are unchanged (their `requires` already match the backend reads).
- **`totp_required_for_admin_role`** (D9): a new dashboard setting (Alembic column, `security:write` field). An account is *admin-level* when its role holds any privileged permission (`security:write`, `users:manage`, `roles:manage`, `accounts:export`, `conversations:read`, `audit:read`) — the admin preset by definition, a custom role as soon as it holds one. When the option is on, admin-level accounts without a TOTP secret are issued sessions in the existing `totp_enrollment_required` state; Operators and Viewers are unaffected. The session dependency and the session response share one predicate (`totp_policy_applies`).
- **Enable guard reads the acting account**: turning on either `totp_required_on_login` or `totp_required_for_admin_role` requires the acting account to hold a TOTP secret (same `400 invalid_totp_config` answer as today); the legacy `dashboard_settings.totp_secret_encrypted` is no longer consulted. `totpConfigured` in the settings response now means "the acting account has TOTP". The response gains `usersWithoutTotpCount` and `adminsWithoutTotpCount` so the UI can say how many people will have to enrol.
- **People tab "Sign-in requirements" row**: keeps the global statement and gains a **Require two-factor for administrators** toggle (rendered only with `security:write`) with the "N administrators will have to enrol at next sign-in" hint and the enable guard worded inline ("Set up your own two-factor first.").
- **Docs**: `docs/authentication.md` describes what Operator and Viewer can do now that they are assignable, and the admin two-factor requirement.

Not in this change: step-up authentication, SSO/IdP wording, providers, new navigation items, any change to the guest UI, own-scoped (Member) views.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: admin-level definition; `totp_required_for_admin_role` semantics and enable guard; `PUT /api/settings` security-field list; settings response enrolment counts; per-user TOTP requirement text covers both toggles.
- `frontend-architecture`: permission-based gating of pages and controls (replacing `write`/`admin` role checks); People tab sign-in requirements row; Conversations view gated by `conversations:read`.

## Impact

- `app/core/auth/dashboard_access.py` (`is_admin_level`, `totp_policy_applies`), `app/core/auth/dependencies.py`, `app/modules/dashboard_auth/service.py`, `app/modules/settings/{schemas,service,repository,api}.py`, `app/db/models.py`, `app/db/alembic/versions/20260910_000000_add_totp_required_for_admin_role.py`
- `frontend/src/features/{dashboard,accounts,apis,settings,api-keys}/components/*`, `frontend/src/features/settings/{schemas,payload}.ts`, `frontend/src/i18n/locales/*.json`, `frontend/src/test/mocks/factories.ts`
- Wire: `GET/PUT /api/settings` gain `totpRequiredForAdminRole`; `GET /api/settings` gains `usersWithoutTotpCount`, `adminsWithoutTotpCount`; `totpConfigured` is now per acting account. No new environment variables.
