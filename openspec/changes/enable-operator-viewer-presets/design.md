## Context

The role tables (`PRESET_ROLE_GRANTS`) and the route gates (`require_dashboard_permission`) already separate Operator and Viewer from Admin on the backend, and `manage-dashboard-users-and-invites` lets an admin assign those roles. The dashboard, however, still branches on two coarse facts: the `write` alias (`canWrite`) and the wire role (`role === "admin"`, which every signed-in account carries). The only TOTP policy is the global `totp_required_on_login`, whose enable guard still reads the legacy `dashboard_settings.totp_secret_encrypted` column (a leftover of `user-login-and-session-v2`).

## Goals / Non-Goals

**Goals**
- An Operator or Viewer signing in for the first time sees only what their routes honour; nothing they can click answers 403.
- One more two-factor policy — for administrators — with one definition of "administrator" shared by the session gate and the session response.
- The enable guards and `totpConfigured` describe the acting account.
- Pin the Operator/Viewer route contract with product-path tests, not hand-built principals.

**Non-Goals**
- Step-up authentication, providers, SSO, break-glass (later Phase 2 PRs); new navigation items; guest UI changes; own-scoped views.

## Decisions

### "Admin-level" is a property of the grant table, not of the role row

`is_admin_level(grants)` is true when the grants intersect `PRIVILEGED_PERMISSIONS`. The admin preset qualifies because it holds everything; a custom role qualifies as soon as it holds one privileged permission; Operator, Member, Viewer and Guest never do. Deriving it from grants means a custom role gets the requirement the moment it becomes dangerous, with no flag to keep in sync, and an upgrade that adds a privileged permission to the vocabulary widens the definition by construction.

### One predicate for the session gate and the session response

`totp_policy_applies(required_on_login, required_for_admin_role, grants)` lives next to the grant tables and is called from `validate_dashboard_session`, the dashboard-auth service's management-session checks, the login-success audit branch and `describe_session`. The previous code computed `totp_policy_applies = settings.totp_required_on_login` once before the user was resolved; it now needs the user's grants, so the resolution order inside `_user_session_principal` changed (grants first). `requires_auth` — whether a passwordless install must demand a login at all — deliberately keeps following only the global toggle: the admin-role toggle binds accounts, and an install without accounts has none.

### The enable guard fires on the transition, and reads the actor

`if payload.flag and not current.flag` for either toggle, then the acting account must hold a TOTP secret. Checking the flag's value on every save (as the old guard did) would refuse every settings save by an Operator while the admin-role requirement is on. The actor is resolved from the same user list that produces the enrolment counts, so one query serves the guard, `totpConfigured` and both counts. The implicit local admin (no account) has no secret and therefore cannot enable either toggle — as before, when the legacy column was empty on a passwordless install. The status code and code name stay `400 invalid_totp_config`: the frontend already matches that code, and older clients keep their behaviour.

### Account mutations require `accounts:write`, not the coarse alias

PLAN §4.1 assigns account CUD, pause, probe, import and routing to `accounts:write`. The account routes still checked `require_dashboard_write_access` (the alias = `accounts:write` ∧ `api_keys:write` ∧ `ops:write`), so a custom role granting `accounts:write` alone would see controls the dashboard gates on `accounts:write` and get 403. The pure account mutation routes — the accounts router's mutations, the two reset-credit consume routes and the OAuth add-account flow — now require `accounts:write`; API keys, settings, model sources and the other alias-gated routes are untouched. A guest is answered `403 permission_required` naming `accounts:write` on those routes instead of `read_only_access`.

### `totpConfigured` leaves the settings response

The settings response is cached under one query key that survives an account change; a per-account field there would show the previous account's state to whoever signs in next within the cache window. The session response already carries `totpConfigured` for the signed-in account and is refreshed on every sign-in, so the TOTP card reads it from the auth store and the settings response drops the field.

### API-key mutation controls follow the alias the routes check

The API-key page opens on `api_keys:read`, but its mutation routes still run behind `require_dashboard_write_access`. `ApiList`/`ApiDetail` therefore take `readOnly` from `canWrite`: a custom role granting `api_keys:read` alone sees the list and the overview without create, edit, regenerate, enable/disable or delete.

### Personal controls follow the account, not the alias

A Viewer holds no `write`, yet owns a password and (with a requirement on) a TOTP secret. The password and TOTP cards, the Access card mount and the header's My two-factor now follow `passwordManagementEnabled && passwordSessionActive` (the password card additionally renders for the implicit admin holding `write`, who has no password yet); guest access, session length and both requirement toggles stay on `security:write`.

### The global toggle waits for the compat admin (N+1 removal)

Enabling `totp_required_on_login` while the migrated `admin` account has a password and no secret writes exactly the legacy-row state a previous-release replica cannot handle (`totp_required_on_login=true` + NULL secret), the same reason `reset-totp` refuses that account. The off→on transition of the global toggle therefore answers `409 compat_user_locked` in that state; the admin-role toggle is not mirrored and needs no lock. Both locks go with the legacy mirror in release N+1. Solo password removal (`clear_user_credentials`) resets both requirements, so a re-bootstrapped install never starts at the enrolment gate.

### `SettingsService` reads users through its own repository

`SettingsRepository.list_active_password_users()` delegates to `DashboardUsersRepository` on the same session rather than adding a second repository to `SettingsContext`; the settings module already owns the response that reports the counts. Mapping the settings row to `DashboardSettingsData` was duplicated in `get_settings` and `update_settings`; both now call `_to_data(row, totp)`.

### The compat-admin reset lock follows the global flag only

`reset-totp` on the migrated `admin` account stays refused only while `totp_required_on_login` is on. That lock exists because a previous-release replica reads the legacy columns and would refuse the account forever; such a replica does not know `totp_required_for_admin_role`, and on this release a reset simply parks the admin at the enrolment gate at next sign-in, which is the intended flow. The People tab's fail-closed rule keeps reading the settings query (both flags arrive together), gating the compat row on the global flag.

### Frontend: the permission the route needs, nothing coarser

Each surface reads `usePermission(<permission>)` for the permission its backend route demands. Where a component mixes concerns, it gets one extra prop rather than a second store read: `ApiKeysSection.policyControlsDisabled` (the two toggles are security fields, key management is `api_keys:write`), `TotpSettings.canEditPolicy` (the requirement toggle is a security field, the personal secret is not), `UpstreamProxySettings.canCreateEndpoint` (endpoint creation is `security:write`, pools are `write`). `FirewallSection` is disabled rather than hidden so an Operator can still see the allowlist the read route serves. Navigation `requires` are unchanged: Automations' reads are session-only, so `dashboard:read` is right.

### The admin-TOTP toggle lives in the People tab, next to the statement it extends

The row already stated the global requirement; it now adds the toggle as its own line, rendered only with `security:write` (a `users:manage` holder without it sees the statement alone). The toggle saves through its own mutation and invalidates the shared `["settings","detail"]` query rather than threading `onSave` through the card, because the People tab is also rendered on `/settings/access` where no Settings form exists. The 400 `invalid_totp_config` refusal is worded in the tab's own terms; other refusals show the server message.

## Risks / Trade-offs

- An install that turns the admin-role requirement on locks every other admin-level account out until they enrol. The hint "N administrators will have to enrol at next sign-in" is shown before the toggle is flipped, and the guard guarantees the person flipping it is not among them.
- `totpConfigured` changed meaning (legacy row → acting account). The only consumer is the TOTP card, which now shows the right state for non-compat accounts; older clients that read it see a value at least as accurate as before.
