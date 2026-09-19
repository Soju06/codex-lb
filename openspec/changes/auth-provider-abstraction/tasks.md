## 1. Schema and providers

- [x] 1.1 `DashboardAuthProvider` model, `AuthProviderKind`, invite `expected_*` columns; Alembic `20260910_010000_add_dashboard_auth_providers` (create + insert-ignore seed + invite columns; downgrade drops both); test-schema seeding.
- [x] 1.2 `app/core/auth/providers`: `ExternalIdentity`, `AuthProvider` protocol (documented `begin_login`/`complete_login` extension points), `PasswordProvider`, `TrustedHeaderProvider`; registry with `provider_active` and a 5 s cache invalidated through the `dashboard_users` namespace.

## 2. Resolver and request path

- [x] 2.1 `IdentityResolver` (steps 1 / 1.5 / 2 / 3, JIT username rules with the reserved `admin`, audit `identity_linked` / `user_created` / `login_failed reason=unknown_identity`) and the per-identity resolution cache.
- [x] 2.2 `resolve_trusted_header_request`; the session dependency's trusted-header branch yields a user principal or `401 identity_not_provisioned` / `account_disabled`; `user_principal` carries `auth_mode`.
- [x] 2.3 Session response: `login.providers` (active providers, `provider_key`), `login.pending_identity`, trusted-header account block; header-less requests show the proxy notice until a password account exists; `create_first_admin` counts password holders only.

## 3. API

- [x] 3.1 `GET/PATCH /api/auth-providers` (`security:write`, attributable account, assignability + delegation on role ids, audit `provider_updated`, registry invalidation) and the route matrix declarations.
- [x] 3.2 `POST /api/dashboard-users` `ssoOnly`/`expectedIdentity` (`409 sso_not_available`, `409 identity_taken`, `422` without an identity), `invite: null`, `ssoOnly` on pending invites.

## 4. Frontend

- [x] 4.1 `/auth/pending` public route and `PendingIdentityScreen`; AuthGate parks `login.pendingIdentity` sessions there.
- [x] 4.2 Invite dialog "Add without a password" with the identity field; pending invites mark SSO-only rows and hide resend; Access gates follow `user !== null`; i18n en/ko/zh-CN; MSW handlers.

## 5. Verification and docs

- [x] 5.1 Unit: slug rules, candidates, header identity parsing, `provider_active`. Integration: JIT admin default (D10), `admin-2`, refused identity + pending session + audit, disabled account, no re-evaluation, throttle, SSO-only linking, `sso_not_available`, `link_by_email` on/off, provider API (list, patch, gates, audit, delegation), migration up/down; existing auth/users/audit/migration suites; route matrix.
- [x] 5.2 Frontend: pending screen (route and `pendingIdentity`), invite dialog SSO option, Access card/page with a proxy account; lint, typecheck, full vitest.
- [x] 5.3 `docs/authentication.md` trusted-header section; `openspec validate auth-provider-abstraction --strict`.
