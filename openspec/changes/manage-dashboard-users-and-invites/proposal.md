## Why

Since `user-login-and-session-v2` every dashboard sign-in is an account in `dashboard_users`, but the only way to get an account is the first-run setup that creates `admin`. There is no way to add a second person, hand them a role, take a role away, disable someone who left, or reset a lost TOTP secret. The plan (PLAN.md §4.2, §4.5, PR-1c) makes this the first user-management slice: an invite-link flow (the admin never learns anyone's password), the account invariants in one service, the owned-API-key cascade on disable/delete, and a read-only roles API so the frontend can render role pickers from one source.

## What Changes

- **Schema** (`20260909_040000_add_dashboard_user_invites`): `dashboard_user_invites` (hash-only single-use token, one live invite per user, `created_by_user_id` snapshot, `sso_only`/`username_locked` flags). No other schema change.
- **Delegation primitives** in `app/core/auth/dashboard_access.py`: `assert_can_delegate(caller, target_role_grants)` and `assert_can_act_on(caller, target_user_grants)` — every (permission, scope) of the target must be at or below the caller's grant (`own` < `all`, missing is below both). API layer maps `InsufficientDelegationError` to `403 insufficient_delegation`.
- **Management API** `/api/dashboard-users` (`users:manage`; every mutation also requires the caller to be an account → `409 admin_account_required`): list, create-with-invite (`201 {user, invite: {token, expiresAt}}`, token shown once), PATCH (role / display name / e-mail / status with self, last-admin, delegation and compat rules; `status=disabled` cascades owned keys to `is_active=false, deactivated_reason='owner_disabled'` and ends sessions), DELETE (keys stay inactive with `owner_user_id` cleared), invite resend (token rotation) / revoke (deletes an `invited` account) / pending list with lazy purge of expired invited accounts, `reset-totp`, `revoke-sessions`, `reactivate-keys` (only `owner_disabled` keys).
- **Public invite routes** on `/api/dashboard-auth`: `GET /invite/{token}` (one `404 invite_not_found` for every invalid token, 30/60 s per IP) and `POST /invite/accept` (`409 already_signed_in` with a valid session cookie, compare-and-set consumption, same password policy as setup, 8/60 s per IP and 5/60 s per token, issues a v2 session). `PATCH /api/dashboard-auth/me` for self-service display name / e-mail. `access_summary.pending_invites` becomes a real count.
- **Roles read API** `/api/dashboard-roles` (`users:manage`): the five presets with their code-resolved grants, lock flag, code-truth assignability and user counts; `/permissions` returns the vocabulary with descriptions, dependency pairs, own-scope support and privileged flag from the single code source.
- **Audit**: `user_created`, `user_invited`, `user_updated`, `user_role_changed` (from/to), `user_disabled`, `user_enabled`, `user_deleted`, `invite_resent`, `invite_revoked`, `invite_accepted`, `user_totp_reset`, `user_sessions_revoked` (scope=admin), `user_keys_deactivated`, `user_keys_reactivated`, all attributed with `AuditActor.from_principal` and `("user", id)` targets.
- **Credential-required guard** `assert_credential_remains` shared by the self-service password removal and the management paths.

Out of scope this release: `sso_only` / `expected_identity` on create (no providers exist; the fields are rejected as unknown), `GET /api/dashboard-users/{id}/api-keys` (dropped for the line budget; the API-key list already exists), `GET /{id}/effective-access`, role writes, any frontend.

## Capabilities

### New Capabilities

- `dashboard-users` gains its first management requirements (the capability already holds the login-source-of-truth requirement from the previous change).

### Modified Capabilities

- `admin-auth`: invite acceptance routes, `already_signed_in`, invite rate limits, token-path log redaction, `PATCH /me`, real `pending_invites`.
- `api-keys`: `PATCH` maintains `deactivated_reason` (`manual` on revoke, cleared on re-enable, `409 owner_disabled` for keys of a disabled owner).
- `dashboard-roles`: read API and permission descriptors.

## Impact

- `app/db/models.py`, one Alembic revision, `app/core/auth/dashboard_access.py`, `app/modules/dashboard_users/{api,service,schemas,repository,credentials}.py`, `app/modules/dashboard_roles/{api,schemas,service,repository}.py`, `app/modules/dashboard_auth/{api,service,schemas,repository}.py`, `app/dependencies.py`, `app/main.py`.
- Wire: new routes and additive fields only. No new setting, env var, README section or nav item. Audit table stays append-only. Zero-config: a single-account install sees nothing new until it invites someone.
