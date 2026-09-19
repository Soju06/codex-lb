## Why

A reverse-proxy (`trusted_header`) install still treats every identity the proxy vouches for as one anonymous implicit admin: no account row, no attribution in the audit log, no way to give one person less than everything, and no way to pre-create an account for a colleague. PLAN.md §4.6 (PR-2c-1) turns the way people sign in into *providers* (password today, the proxy header today, OIDC later) that all end in one identity → account path, so the reverse-proxy install gets real accounts without changing what its users experience: unknown identities still become admins by default (owner decision D10), just as their own account.

## What Changes

- **Providers**: an `AuthProvider` protocol (`kind`, `provider_key`, `resolve_identity(request)`; `begin_login`/`complete_login` documented as the redirect-style extension points) with `PasswordProvider` (marker) and `TrustedHeaderProvider` (reads the configured proxy header, case-folds the subject, parses an e-mail when it looks like one). A cached registry joins the new `dashboard_auth_providers` rows with `CODEX_LB_DASHBOARD_AUTH_MODE` to answer which providers are *active*; the mode values are unchanged and nothing is persisted about the mode.
- **Schema**: `dashboard_auth_providers` (kind, provider_key, enabled, label, config_encrypted, unknown_identity_role_id, no_match_role_id, link_by_email, skip_role_sync, idp_mfa_enforced) seeded with `password/default` and `trusted_header/default` (unknown identities → admin preset, D10; no match → viewer). `dashboard_user_invites` gains `expected_provider/expected_provider_key/expected_subject`.
- **IdentityResolver**: identity row → pre-created invited account with the exact expected identity → e-mail link (provider opt-in) → just-in-time account with the provider's default role (`NULL` refuses). Accounts are never resolved by username; existing accounts are not re-evaluated (no mappings exist yet); results are cached per identity for the users-cache TTL.
- **Request path**: a trusted-header request resolves to a **user principal** (the account's grants, `auth_method=trusted_header`, audited with the account id); the implicit-admin shortcut for the header is removed. Refused identities answer `401 identity_not_provisioned` (or `account_disabled`), and the session response reports `login.pending_identity=true` so the frontend renders `/auth/pending`. The first-run password setup now refuses only when an active account already *holds a password*, so the local `admin` stays creatable next to proxy accounts as the break-glass fallback.
- **API**: `GET /api/auth-providers` and `PATCH /api/auth-providers/{id}` (`security:write`, an attributable account) for the resolver knobs; `POST /api/dashboard-users` accepts `ssoOnly` + `expectedIdentity` (only while a non-password provider is active) and returns `invite: null` for SSO-only accounts; pending invites carry `ssoOnly`; the session's `login.providers` lists the active providers.
- **Frontend**: `/auth/pending` public route ("Your account is not ready yet"); the invite dialog offers "Add without a password" with an identity field when a non-password provider is active; pending invites mark SSO-only rows; the People tab, the solo line and `/settings/access` gate on "the session has an account" instead of "standard mode", because a proxy account is an account.
- **Docs**: `docs/authentication.md` explains per-identity accounts, the default role and how to change it, and the pending screen.

Not in this change: role mappings and the groups header env var, `no_match_role_id`/`skip_role_sync` behaviour (stored, not yet acted on), the organisation-integration UI, the local login policy, OIDC, TOTP step-up for header sessions (PR-2b).

## Capabilities

### New Capabilities

- `identity-providers`: the providers table and its defaults, the registry/active rule, the identity → account resolution steps and JIT rules, the throttle, and the provider settings API.

### Modified Capabilities

- `admin-auth`: trusted-header requests resolve to accounts (the implicit trusted-header admin is gone); `identity_not_provisioned`/`account_disabled`; the session response's `login.providers`, `login.pending_identity`; first-run setup counts password holders only.
- `dashboard-users`: `POST /api/dashboard-users` `ssoOnly`/`expectedIdentity`, `invite: null`, `ssoOnly` on pending invites.
- `frontend-architecture`: `/auth/pending` public route; the invite dialog's password-less option; Access card / full page gates follow the account, not the mode.

## Impact

- `app/core/auth/{providers/,external_identity.py,dependencies.py,dashboard_access.py}`, `app/modules/auth_providers/*`, `app/modules/dashboard_users/{identity_resolver,repository,service,schemas,api}.py`, `app/modules/dashboard_auth/{api,schemas,service,repository}.py`, `app/modules/dashboard_roles/service.py`, `app/db/models.py`, `app/db/alembic/versions/20260910_010000_add_dashboard_auth_providers.py`, `app/dependencies.py`, `app/main.py`
- `frontend/src/features/auth/{schemas.ts,components/auth-gate.tsx,components/pending-identity-screen.tsx}`, `frontend/src/features/access/api.ts`, `frontend/src/features/settings/components/access/*`, `frontend/src/i18n/locales/*`, `frontend/src/test/mocks/*`
- `docs/authentication.md`; tests under `tests/unit`, `tests/integration`, `frontend/src/**/*.test.tsx`
- No new `CODEX_LB_*` setting; `dashboard_auth_mode` values unchanged; one Alembic revision on the current head.
