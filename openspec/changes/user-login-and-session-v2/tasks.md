## 1. Users as the auth source of truth

- [x] 1.1 `DashboardUsersRepository` (`local_auth_state`, counts, lookups) and `DashboardUsersCache` (5 s TTL, `dashboard_users` namespace, registered in `app/main.py`, reset in the test harness).
- [x] 1.2 `DashboardAuthRepository` rewritten around the user row with same-transaction legacy mirroring for the compat admin (password, TOTP secret, replay counter both-or-neither, credential clear, first admin creation, generation bump, last login).
- [x] 1.3 Bootstrap-token functions and `POST /password/setup` derive "password configured" from `local_auth_state()`.
- [x] 1.4 Revision `20260909_020000_reproject_compat_admin_credentials` (data-only, no-op downgrade).

## 2. Sessions

- [x] 2.1 Cookie payload v2 (`uid`/`sg`/`pv`/`tp`/`am`, guest `gg`); v1 rejected; `create_user_session` / `create_guest_session`.
- [x] 2.2 `validate_dashboard_session` resolves the account (active, generation match), builds `user_principal`, enforces TOTP verification and the `totp_enrollment_required` state; implicit local admin carries `auth_method=local_bootstrap`.
- [x] 2.3 `DashboardPrincipal` gains `user_id`, `username`, `role_slug`, `auth_method`, `totp_enrollment_required`.
- [x] 2.4 Admin-preset sessions capped at 12 h unless trusted-local.

## 3. Login and self-service API

- [x] 3.1 `POST /password/login {username?, password}`: sole-user resolution, `422 username_required`, identical failure bodies, per-username limiter with break-glass exemption.
- [x] 3.2 Per-user TOTP endpoints (start/confirm/verify/disable) with the username as the otpauth label.
- [x] 3.3 `POST /password/change` bumps the generation and re-issues the cookie; `DELETE /password` solo-only (`409 other_users_exist`); `POST /logout-all`; `GET /me`.
- [x] 3.4 Session response additions (`user`, `auth_method`, `must_change_password`, `totp_enrollment_required`, `login`, `access_summary`, `assignable_role_ids`, scoped `permissions`).

## 4. Verification

- [x] 4.1 Unit: session store v2, dependency branches with a users-cache fake, service with an in-memory user repository, API TTL/limiter behaviour, bootstrap state.
- [x] 4.2 Integration (product path): bootstrap creates `admin`; sole-user login; explicit/unknown usernames; second account → `username_required`; per-username limiter; password change re-issues cookie; logout-all; disabled account and generation bump; v1 cookie rejected; per-user TOTP incl. mirroring, replay and enrolment state; solo-only password removal; `/me`; session response shape; guest flows unchanged; bootstrap lifecycle; re-projection migration.
- [x] 4.3 `ruff`, `ty`, focused pytest, PostgreSQL drift contract, `openspec validate --strict`.
- [x] 4.4 `docs/authentication.md` updated (roles, signing in, re-login after upgrade, `logout-all`).
