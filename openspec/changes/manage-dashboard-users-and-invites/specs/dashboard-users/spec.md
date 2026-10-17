## ADDED Requirements

### Requirement: Account management API

The dashboard SHALL expose `/api/dashboard-users` gated by `users:manage`. `GET /api/dashboard-users` SHALL list every account including `status=invited` rows as `{id, username, displayName, email, role: {id, slug, name, kind}, roleSource, status, isBreakGlass, totpConfigured, hasPassword, createdAt, lastLoginAt, pendingInvite: {expiresAt} | null}` and MUST never include a password hash, TOTP secret, or invite token. Every mutating route (`POST`, `PATCH`, `DELETE`, and the `invite`, `reset-totp`, `revoke-sessions`, `reactivate-keys` actions) MUST additionally require the caller to be an account (`principal.user_id` set) and MUST answer `409 admin_account_required` for the implicit local admin, the disabled-auth principal, and a trusted-header principal without an account. Every mutation MUST write an audit row attributed with the caller's actor and a `("user", <id>)` target. Every mutation that changes an account row MUST invalidate the `dashboard_users` cache namespace; `reactivate-keys` changes no account row and invalidates only the affected API-key cache entries.

#### Scenario: Implicit local admin may read but not mutate

- **GIVEN** a local install without a dashboard password
- **WHEN** the caller lists accounts and then posts a new account
- **THEN** the list answers `200` and the post answers `409 admin_account_required`

#### Scenario: Listing never carries secrets

- **WHEN** an admin lists accounts
- **THEN** no row contains a `passwordHash`, TOTP secret, or token field, and invited rows carry `pendingInvite.expiresAt`

### Requirement: Adding a person issues a one-time invite

`POST /api/dashboard-users` `{username, displayName?, email?, roleId, usernameLocked?}` SHALL normalise the username (trim, casefold) and refuse an invalid shape with `422 validation_error`, refuse a taken username with `409 username_taken` and a taken e-mail with `409 email_taken`, refuse a role that does not exist or is not assignable (presets outside `admin`, `operator`, `viewer`; custom roles with `assignable_to_users=false`) with `422 role_not_assignable`, and refuse a role whose grants exceed the caller's (`assert_can_delegate`) with `403 insufficient_delegation`. Unknown fields (including `ssoOnly` and `expectedIdentity`) MUST be rejected with `422`. On success it SHALL create the account with `status=invited`, `role_source=manual`, `created_by_user_id=<caller>` and one invite valid for 24 hours whose SHA-256 is stored, and SHALL answer `201 {user, invite: {token, expiresAt}}` — the only time the plaintext token is returned. It SHALL audit `user_created` (role slug) and `user_invited`.

#### Scenario: Create returns the token once

- **WHEN** an admin creates `Bob` with the viewer role
- **THEN** the response is `201` with `user.username == "bob"`, `user.status == "invited"`, and a non-empty `invite.token`
- **AND** a later listing shows the account with `pendingInvite` but no token

#### Scenario: Duplicate and invalid input

- **WHEN** an admin creates a second account with the same username in another case, or the same e-mail
- **THEN** the responses are `409 username_taken` and `409 email_taken`
- **AND** creating an account with the `guest` or `member` role answers `422 role_not_assignable`

#### Scenario: Delegation

- **GIVEN** a caller whose role holds `users:manage` but not the admin grants
- **WHEN** it creates an account with the admin role
- **THEN** the response is `403 insufficient_delegation`
- **AND** creating an account with the viewer role succeeds

### Requirement: Editing an account enforces the account invariants

`PATCH /api/dashboard-users/{id}` `{roleId?, displayName?, email?, status?: "active"|"disabled"}` SHALL apply these rules in this order: a role or status change on the migrated compat `admin` account → `409 compat_user_locked` (display name and e-mail stay editable); a role or status change on the caller's own account → `409 self_modification_forbidden`; a status change on an `invited` account → `409 invite_pending`; a role change → `assert_can_delegate` on the new role; any change on another account → `assert_can_act_on` on the account's current role (`403 insufficient_delegation`); a change that would leave zero active accounts holding the admin *preset* → `409 last_admin_protected` (custom roles never count); `status=active` on an account with no password and no identity → `409 credential_required`; `status=invited` → `422`. The last-admin rule MUST be part of the write: the role/status UPDATE of an account that currently counts as an active admin MUST be conditional on another active admin-preset account existing at that moment, and a refused write MUST roll back the whole transaction including the key cascade. Concurrent account mutations MUST be serialised (SQLite: write-intent transaction; PostgreSQL: the active admin rows are locked `FOR UPDATE` first) so two admins disabling each other end with exactly one success. A role change and a disable MUST increment the account's `session_generation`. `status=disabled` MUST, in the same transaction, set every active key with `owner_user_id == id` to `is_active=false, deactivated_reason='owner_disabled'` and MUST invalidate the API-key cache for those keys; `status=active` MUST NOT restore keys. E-mail uniqueness applies (`409 email_taken`, also when a concurrent writer wins the race after the pre-check). The response SHALL use the listing shape, including `pendingInvite` for an invited account. Audit: `user_role_changed` (details `from`/`to` slugs), `user_disabled` plus `user_keys_deactivated` (details `count`), `user_enabled`, `user_updated`.

#### Scenario: Concurrent cross-disables keep one admin

- **GIVEN** two active non-compat admins
- **WHEN** each disables the other at the same time
- **THEN** exactly one request answers `200`, the other `409 last_admin_protected`, and one active admin remains

#### Scenario: Compat admin keeps role and status

- **WHEN** any caller changes the `admin` account's role or status
- **THEN** the response is `409 compat_user_locked`, while a display-name edit succeeds

#### Scenario: Role change ends the target's sessions

- **GIVEN** an operator is signed in
- **WHEN** an admin changes the operator's role to viewer
- **THEN** the operator's next request answers `401` and an audit row `user_role_changed` records `from: operator, to: viewer`

#### Scenario: Self and last-admin protection

- **WHEN** a non-compat admin changes their own role or disables themselves
- **THEN** the response is `409 self_modification_forbidden`
- **AND** when another account with `users:manage` disables or demotes the only active admin the response is `409 last_admin_protected`

#### Scenario: Disable cascades owned keys with a reason

- **GIVEN** an account owns an active key and a key that was deactivated manually
- **WHEN** the account is disabled
- **THEN** the active key becomes inactive with `deactivated_reason = owner_disabled`, the manual key keeps its reason, and the key cache entry is dropped

### Requirement: Deleting an account

`DELETE /api/dashboard-users/{id}` SHALL refuse the caller's own account with `409 self_modification_forbidden`, the migrated compat `admin` account with `409 compat_user_locked`, and the last active admin-preset account with `409 last_admin_protected`, then apply `assert_can_act_on`. The DELETE of an account that counts as an active admin MUST be conditional on another active admin existing at that moment (a refused write rolls the whole transaction back). On success it SHALL deactivate the account's active keys with `deactivated_reason='owner_disabled'`, clear their `owner_user_id`, delete the account with its identities and invite, answer `204`, and audit `user_deleted` keeping the username as text.

#### Scenario: Compat admin cannot be deleted

- **WHEN** any caller deletes the `admin` account
- **THEN** the response is `409 compat_user_locked`

#### Scenario: Keys survive their owner, inactive

- **GIVEN** an operator owns an active key
- **WHEN** an admin deletes the operator
- **THEN** the key row remains with `owner_user_id` NULL, `is_active` false, `deactivated_reason = owner_disabled`, and the operator's sessions are refused

### Requirement: Invite lifecycle on the management side

`POST /api/dashboard-users/{id}/invite` SHALL be allowed only for `status=invited` accounts (`409 invite_not_pending` otherwise), SHALL apply `assert_can_act_on`, SHALL replace the invite's token hash and expiry with an UPDATE conditional on the account still being `invited` at that moment (an acceptance that committed meanwhile answers `409 invite_not_pending` and its consumed invite is never re-armed; the previous link stops working immediately), SHALL answer `{token, expiresAt}`, and SHALL audit `invite_resent`. `DELETE /api/dashboard-users/{id}/invite` SHALL delete the account row together with the invite when the account is `invited` (an invited account has no credential, keys, or sessions) with a DELETE conditional on `status = 'invited'` (an account accepted meanwhile survives and the call answers `409 invite_not_pending`), SHALL mark a still-live invite of any other account revoked, SHALL answer `409 invite_not_pending` when there is nothing to revoke, SHALL answer `204`, and SHALL audit `invite_revoked`. `GET /api/dashboard-users/invites` SHALL list live invites (`{userId, username, roleId, expiresAt, createdByUserId}`). An invite whose `expires_at` has passed is not live; invited accounts with an expired invite are zombies: they MUST NOT be counted in `access_summary`, MUST be deleted lazily (by one DELETE whose `status = 'invited'` and no-live-invite predicates are evaluated in the deleting transaction, so an acceptance that committed meanwhile is never swept up) before the account list, the pending-invite list, and every account mutation (create, edit, delete, resend, revoke, reset-totp, revoke-sessions — a mutation targeting a zombie answers `404 user_not_found`) — there is no background job.

#### Scenario: Resend rotates the token

- **WHEN** an admin resends an invite
- **THEN** the previous token answers `404 invite_not_found` and the new token is valid

#### Scenario: Revoke or resend after a concurrent acceptance

- **GIVEN** the invitee accepted between the admin's read and the write
- **WHEN** the admin revokes or resends the invite
- **THEN** the response is `409 invite_not_pending` and the activated account is untouched

#### Scenario: Revoke removes the invited account

- **WHEN** an admin revokes the invite of an invited account
- **THEN** the response is `204`, the account no longer exists, and the token answers `404`

#### Scenario: Expired invites are purged on the next read

- **GIVEN** an invited account whose invite expired
- **WHEN** an admin lists pending invites
- **THEN** the list is empty and the account row is gone

### Requirement: Account actions

`POST /api/dashboard-users/{id}/reset-totp` SHALL refuse the caller's own account (`409 self_modification_forbidden`; self-service uses `/totp/disable`), SHALL refuse the compat `admin` account with `409 compat_user_locked` while `totp_required_on_login` is on (a previous-release replica reading the legacy columns would refuse that account forever), SHALL apply `assert_can_act_on`, SHALL clear the account's TOTP secret and replay counter (mirroring the compat admin's legacy secret and counter WITHOUT changing `totp_required_on_login`), increment its `session_generation`, and audit `user_totp_reset`. `POST /api/dashboard-users/{id}/revoke-sessions` SHALL apply `assert_can_act_on`, increment the generation, and audit `user_sessions_revoked` with `scope: admin`. `POST /api/dashboard-users/{id}/reactivate-keys` SHALL require the account to be `active` (`409 user_not_active`), apply `assert_can_act_on`, and set every key with `owner_user_id == id` and `deactivated_reason='owner_disabled'` back to active with the reason cleared by an UPDATE that is serialised with account mutations and conditional on the owner being `active` at that moment (a disable that commits concurrently leaves the keys inactive and the call answers `409 user_not_active`), invalidate their cache entries, answer `{reactivated: n}`, and audit `user_keys_reactivated` (details `count`).

#### Scenario: Administrative TOTP reset keeps the install policy

- **GIVEN** `totp_required_on_login` is on and two admins are enrolled
- **WHEN** one admin resets the other's TOTP
- **THEN** the flag stays on and the other admin's next login is held at `totp_enrollment_required`
- **AND** resetting the compat `admin` account answers `409 compat_user_locked`

#### Scenario: Reset and revoke end sessions

- **GIVEN** an account is signed in
- **WHEN** an admin calls `revoke-sessions` and later `reset-totp`
- **THEN** each call raises the account's `session_generation` and its previous cookie is refused

#### Scenario: Reactivation races a disable

- **WHEN** an admin disables an account while another request reactivates its keys
- **THEN** the end state is consistent: either the owner is active with its keys restored, or the owner is disabled and every owned key is inactive with reason `owner_disabled`

#### Scenario: Reactivation restores only the cascade

- **GIVEN** a re-enabled account with one `owner_disabled` key and one manually deactivated key
- **WHEN** an admin calls `reactivate-keys`
- **THEN** the response is `{reactivated: 1}` and only the cascaded key is active again

### Requirement: Delegation primitives

`assert_can_delegate(caller_grants, target_grants)` and `assert_can_act_on(caller_grants, target_user_grants)` SHALL pass only when every `(permission, scope)` of the target is satisfied by the caller's grant for that permission (`all` satisfies `all` and `own`; `own` satisfies `own`; a missing permission satisfies nothing) and SHALL raise `InsufficientDelegationError` otherwise. The API layer MUST map the error to `403 insufficient_delegation`.

#### Scenario: Preset ordering

- **WHEN** the primitives are applied to the preset grant tables
- **THEN** admin may delegate admin, operator may delegate viewer, and operator may not delegate admin

### Requirement: Credential-required guard

`assert_credential_remains(password_hash, identity_count, solo_install)` SHALL raise `CredentialRequiredError` when the resulting account would hold neither a password nor an identity, except when `solo_install` marks the single-account password removal that returns the install to bootstrap. Callers MUST map the error to `409 credential_required`.

#### Scenario: Guard

- **WHEN** the guard is evaluated with no password, no identity and `solo_install=false`
- **THEN** it raises; with a password, an identity, or `solo_install=true` it passes
