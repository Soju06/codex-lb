## Why

The previous change (`add-dashboard-users-schema`) created `dashboard_users` and mirrored the shared admin credential into an `admin` row, but every sign-in decision still read the legacy `dashboard_settings.password_hash` / `totp_*` columns. Nothing can be per-user (disable an account, revoke one person's sessions, give someone a smaller role) while the shared columns decide who is signed in. This change flips the read side: the user row is the source of truth for authentication, the session cookie names the account it was issued for, and the legacy columns become a write-only mirror kept for replicas of the previous release.

## What Changes

- **Users are authoritative.** `validate_dashboard_session`, the session response, first-run bootstrap, and the bootstrap-token lifecycle derive everything from `dashboard_users` through a new `local_auth_state()` (process cache + `dashboard_users` invalidation namespace). New code never reads `dashboard_settings.password_hash` / `totp_secret_encrypted` / `totp_last_verified_step`; the compat `admin` user's credential writes are mirrored onto them in the same transaction.
- **One-shot re-projection migration.** Revision `20260909_020000_reproject_compat_admin_credentials` copies the legacy credential onto the `admin` row (or clears it when the legacy password is NULL) so a password/TOTP change made by a previous-release replica during the rolling upgrade is not lost when the user row takes over.
- **Session cookie v2.** Payload `{v, exp, iat, uid, sg, pv, tp, am}` for accounts and `{v, exp, iat, guest, gg}` for guests. The v1 keys (`pw`, `tv`, `role`, `gv`) are gone and v1 cookies are rejected, so a cookie can never be accepted by a replica that cannot check the account behind it. Every request re-reads the account: disabled/removed accounts and a bumped `session_generation` end the session.
- **Login by account.** `POST /password/login {username?, password}`: the username may be omitted only while exactly one active account holds a password; otherwise `422 username_required` (no rate-limit budget spent). Failures are indistinguishable for unknown and known usernames in body and in timing (exactly one bcrypt check per attempt). The per-client limiter is unchanged; there is no per-account lockout.
- **Per-user TOTP.** Secrets and replay counters live on the account; the global `totp_required_on_login` means "every account must enrol". An account without a secret under that policy gets a session in the `totp_enrollment_required` state: only `/api/dashboard-auth/*` answers, everything else is `403 totp_enrollment_required`.
- **Session management.** `POST /password/change` bumps the account's `session_generation` and re-issues this caller's cookie; new `POST /api/dashboard-auth/logout-all` revokes every session of the account; `DELETE /password` is allowed only on a solo install (`409 other_users_exist` otherwise) and keeps the account row. Admin-preset sessions are capped at 12 hours unless the request is trusted-local.
- **Session response additions** (all optional, defaults keep old bundles working): `user`, `auth_method`, `must_change_password`, `totp_enrollment_required`, `login` (username-field hint, never a username), `access_summary` (only for `users:manage` holders), `assignable_role_ids`. `permissions` keeps `read`/`write` and adds `<permission>:<scope>` entries. `role` stays `admin|guest`.
- New `GET /api/dashboard-auth/me` for the signed-in account (`401 user_account_required` for guests and implicit admins).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: user-based login resolution, session cookie v2 and account re-validation, per-user TOTP with the enrolment state (explicit self-service allow-list), `logout-all` / `me`, the admin session cap, and the session response additions.
- `dashboard-users`: the users table becomes the authentication source of truth; legacy columns are a write-only mirror; one-shot re-projection revision.

## Impact

- `app/core/auth/dashboard_users_cache.py` (new), `app/core/auth/dashboard_access.py`, `app/core/auth/dependencies.py`, `app/core/auth/dashboard_session_ttl.py`, `app/core/bootstrap.py`, `app/main.py`.
- `app/modules/dashboard_auth/{api,service,repository,schemas}.py` rewritten around the user row; `app/modules/dashboard_users/{repository,compat}.py`; `app/modules/dashboard_roles/repository.py`.
- One data-only Alembic revision. No new setting, env var, README section, nav item, or frontend change; guest behaviour is unchanged on the wire.
- Operators: every browser signs in once more after the upgrade (cookie format change). Docs: `docs/authentication.md`.
