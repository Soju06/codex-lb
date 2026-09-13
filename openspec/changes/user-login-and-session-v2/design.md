## Context

`add-dashboard-users-schema` landed the tables and made the `admin` user row a mirror of the shared credential: legacy columns authoritative, user row written alongside. This change is the second half of that expand/contract: reads move to the user row, and the legacy columns become the mirror. It is the riskiest slice of the RBAC plan because every dashboard request passes through `validate_dashboard_session`, and because two release versions must coexist during a rolling deploy.

## Goals / Non-Goals

**Goals**
- Every authentication decision derives from `dashboard_users`; the legacy credential columns are never read by new code.
- A cookie is bound to an account and can be revoked server-side (disable, generation bump) without a session table.
- A passwordless install behaves exactly as today (implicit local admin, remote bootstrap token, re-setup after password removal).
- Old and new replicas fail closed on each other's cookies instead of accepting them.

**Non-Goals**
- Creating, inviting, disabling or renaming users through the API (PR-1c), `PATCH /me`, `must_change_password` enforcement, admin-only TOTP policy, SSO providers, frontend changes.

## Decisions

### Legacy columns were authoritative through release N; re-project once, then flip

A replica of the previous release may have changed the password or TOTP secret on `dashboard_settings` only (its mirror writes went legacy → user, but a replica older than that wrote legacy only). Revision `20260909_020000_reproject_compat_admin_credentials` therefore copies `password_hash`, `totp_secret_encrypted`, and `totp_last_verified_step` from the legacy row onto the compat `admin` row unconditionally (creating the row when the legacy password exists but the row does not, clearing the row's credential when the legacy password is NULL, never touching `session_generation`, never deleting the row). After that revision the user row is authoritative and the legacy columns are the mirror. Downgrade is a no-op because nothing structural changes.

### Mirror on write, never read

`DashboardAuthRepository` writes the user row and, when the account is the compat `admin`, the legacy columns in the same transaction (`_write_user`), re-applying both on an optimistic-version conflict so the rows cannot diverge. The TOTP replay counter advances on both rows or on neither. Nothing reads the legacy credential back: `grep password_hash|totp_secret_encrypted|totp_last_verified_step app/` shows only models, the mirror writes, the settings service's existing `totp_configured` read, and the migrations.

### `local_auth_state()` replaces every `password_hash is None` read

A frozen `LocalAuthState` (`any_user`, `active_users`, `active_local_password_users`, `requires_auth`, `sole_local_password_user_id`) is computed by `DashboardUsersRepository.local_auth_state()` and served from `DashboardUsersCache` (5 s TTL, `dashboard_users` namespace, registered next to the settings cache) on the request path. `requires_auth` is "an active account holds a password or an external identity", so a passwordless install that later gains an identity-only user stops being anonymous-admin. Bootstrap-token functions compute the same state uncached inside their own session, matching their existing "read the shared state fresh" contract.

### Cookie v2 renames the keys on purpose

The previous store defaulted `role` to `admin` and only type-checked `exp`/`pw`/`tv`, so a v2 payload that kept those key names would validate as an admin session on an old replica, bypassing the account check. Renaming to `pv`/`tp` and adding `uid`/`sg` makes old replicas reject v2 cookies, and the new store rejects anything without `v == 2`. The rolling window therefore costs one re-login per browser instead of an authorization gap. Guest cookies drop `gv`: a guest cookie is only ever minted under the current `guest_session_generation`, and every guest-credential change bumps it, so a matching generation already proves the cookie passed whatever credential applied.

### Password removal keeps the account row

`DELETE /password` clears the account's credentials (and identities) and bumps its generation but keeps the row, so the install is passwordless again with the same account identity. First-run setup then re-arms that row (`CompatAdminProjection.ensure_exists` sets the password on an existing credential-less row) instead of failing on the unique username. "Already configured" for setup means an active account can sign in (`requires_auth`), not "any row exists".

### Username resolution and limiter shape

With exactly one active local-password account the username is implied and the login form can hide the field (`login.username_field = hidden`); the server never sends the username itself. Unknown, inactive and password-less accounts run the same single bcrypt comparison (against a memoised dummy hash) so neither the body nor the timing of a failure reveals which usernames exist. Only the existing per-client limiter (8/60 s) applies: a per-username bucket keyed by client with the same budget could never trip before the per-client one and only added a write per attempt, so it was dropped. Per-account soft delay (PLAN H6, escalating backoff) is deferred to a later change; a per-account lockout is intentionally not implemented because it would be a remotely triggerable denial of service against the admin.

### Revocation and rotation are single atomic writes

`session_generation` is advanced with `UPDATE ... SET session_generation = session_generation + 1` inside the same transaction as the credential write (`_write_user(bump_generation=True)`), never from a possibly stale ORM value, so a delayed logout cannot write a lower generation and resurrect a revoked cookie. Password change is one such write (`rotate_user_password`): the new hash, its legacy mirror and the generation bump commit together or not at all.

### First-run setup trusts the users table only

`create_first_admin` inserts (or re-arms) the `admin` row first, using the unique username as the atomic guard for a same-release race, and then overwrites the legacy columns unconditionally. Keeping the old `password_hash IS NULL` legacy guard would have let a legacy-only hash written by a late previous-release replica wedge setup forever (users say passwordless, legacy says configured).

### Member is not assignable yet; own-scoped roles fail closed

Routers such as accounts and request logs are guarded by `validate_dashboard_session` alone and assume an all-scope reader. Until the self-service phase makes own-scoped routes declare their own requirement (the guard then moves to route level), a user session whose grants lack `dashboard:read` at `all` is refused with `403 permission_required` (`param: dashboard:read`), and `member` is removed from `ASSIGNABLE_PRESET_ROLES`. The preset seed writes `assignable_to_users=false` for `member` on fresh installs; the seed is insert-only, so installs created by `add-dashboard-roles-schema` keep `true` in the row. That is deliberate: the code constant, not the row, is what the API consults (`assignable_role_ids`, and later the user-management validation), so no migration is needed and the row flips when the seed is next reconciled.

### First-run setup is compare-and-set

`create_first_admin` inserts the `admin` row when missing (deterministic id and unique username turn a concurrent insert into an `IntegrityError` → refused) or re-arms an existing row with `UPDATE ... WHERE password_hash IS NULL RETURNING id` (zero rows → refused). Two setups that both pass the auth-state check therefore yield exactly one `200`; the legacy mirror and the bootstrap-token clear run only after the conditional write, in the same transaction.

### Guest cookies are stamped from the verified row

`verify_guest_password` returns the generation of the very settings row it verified against and the route stamps that value; a second read after verification could observe a generation bumped by an admin enabling a guest password and mint a cookie that never satisfied the new credential.

### Wire `role` stays coarse

Every account session reports `role: "admin"` and the coarse `read`/`write` aliases so the current frontend keeps working; the real role travels in `user.role` and the scoped `permissions` entries. Authorization uses the account's resolved grants, so a `viewer` account is already read-only on the server.

## Risks / Trade-offs

- [Risk] A replica of the previous release still serving during the window keeps reading legacy columns. → The mirror keeps them current; the re-projection revision runs before the new code serves, and the deploy runbook drains old replicas first.
- [Risk] The users cache serves a disabled account for up to 5 s on a peer replica. → Same bound the settings cache already accepts for security-bearing settings; every user write bumps the namespace.
- [Trade-off] The settings service's `totp_configured` and the "enable TOTP requirement" guard still read the legacy secret. → For release N the only account is the mirrored `admin`, so the values agree; the admin-role TOTP option (PR-2a) moves them to the user row.
- [Trade-off] `totp_enrollment_required` is reachable this release only through direct settings edits (the API guard requires a configured secret). → Implemented anyway so the invite flow (PR-1c) and the frontend (PR-1d-1) target a finished server contract.
