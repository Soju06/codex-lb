## 1. Schema

- [x] 1.1 `DashboardUserInvite` model (`dashboard_user_invites`: unique `user_id`, unique `token_hash`, tz-aware timestamps, `created_by_user_id` snapshot, `sso_only`, `username_locked`).
- [x] 1.2 Revision `20260909_040000_add_dashboard_user_invites` (parent `20260909_030000_add_audit_actor_columns`): guarded create, downgrade drops.

## 2. Core

- [x] 2.1 `InsufficientDelegationError`, `assert_can_delegate`, `assert_can_act_on` in `dashboard_access.py`.
- [x] 2.2 `assert_credential_remains` guard (`dashboard_users/credentials.py`) used by password removal and status re-enable.

## 3. Management API

- [x] 3.1 `DashboardUsersRepository` writes: invites (live filter, CAS consume, lazy purge), key cascade / reactivation, delete.
- [x] 3.2 `DashboardUsersService`: create-with-invite, update (role / profile / status with invariants), delete, resend, revoke, reset-totp, revoke-sessions, reactivate-keys, describe/accept invite, self profile edit; audit rows and cache invalidation on every mutation.
- [x] 3.3 Router `/api/dashboard-users` with `users:manage` at router level and `require_admin_account` on every mutation; error mapping table.
- [x] 3.4 `DashboardAuthRepository.set_user_totp_secret(bump_generation=)`; `DashboardUserCounts.pending_invites`.

## 4. Public and self-service routes

- [x] 4.1 `GET /api/dashboard-auth/invite/{token}` (30/60 s per IP), `POST /api/dashboard-auth/invite/accept` (already_signed_in, 8/60 s per IP, 5/60 s per token, session issue).
- [x] 4.2 `PATCH /api/dashboard-auth/me`.
- [x] 4.3 `access_summary.pending_invites` real count.

## 5. Roles read API

- [x] 5.1 `GET /api/dashboard-roles`, `GET /api/dashboard-roles/permissions`; `PERMISSION_DESCRIPTIONS` + `permission_descriptors()`.

## 6. Verification

- [x] 6.1 Unit: delegation ordering (admin→admin, operator→admin refused, operator→viewer, own<all, missing, custom), act_on, credential guard, descriptor completeness.
- [x] 6.2 Integration (httpx product path): admin_account_required, list shape, create/validation/uniqueness/assignability, delegation via custom manager role, invite lookup/accept/replay/already_signed_in/locked username/rate limits, resend rotation, revoke deletes, lazy purge, role change audit + generation bump, self/last-admin/compat refusals, key cascade + reactivate + cache invalidation, delete leaves keys inactive, reset-totp/revoke-sessions, PATCH /me, roles API shape, migration up/down on SQLite, route matrix declarations.
- [x] 6.3 `ruff`, `ty`, focused pytest, `openspec validate manage-dashboard-users-and-invites --strict`, docs section.
