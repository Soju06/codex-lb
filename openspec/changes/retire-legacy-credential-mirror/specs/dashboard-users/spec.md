## REMOVED Requirements

### Requirement: Legacy credential columns are a projection of the admin user

**Reason**: The expand half of the expand/contract is over. Nothing has read the legacy `dashboard_settings` credential columns since `user-login-and-session-v2`, the one-shot revision `20260909_020000_reproject_compat_admin_credentials` has already copied them onto the `admin` account row, and this change drops the columns. Keeping the projection would keep a second writer for a credential with one owner, and keeping the columns without the projection would leave an unowned copy of a live password hash and an encrypted TOTP secret that no code path ever clears or rotates.

**Migration**: `dashboard_users` is the sole authority for every credential (`The users table is the authentication source of truth`). The TOTP replay counter advances on the account row alone and still refuses a reused code. The mirrored install-wide writes that were never about the mirror — bootstrap-token invalidation and the TOTP requirement resets — move onto their own settings path (`Credential paths write install-wide settings without keying on an account name`). The columns themselves are dropped by `Legacy dashboard credential columns are dropped`, whose upgrade order is a stated contract because a replica of any earlier release cannot survive it.

## MODIFIED Requirements

### Requirement: The users table is the authentication source of truth

`dashboard_users` SHALL be the only place the system reads or writes dashboard credentials and account state: password hashes, TOTP secrets and replay counters, `status`, and `session_generation`. No credential write MAY have a second destination: the legacy `dashboard_settings` credential columns are gone, nothing SHALL re-introduce a mirror, and the account's name MUST NOT decide whether a credential write happens. Advancing the TOTP replay counter MUST be a single conditional `UPDATE` of the account row (`totp_last_verified_step IS NULL OR totp_last_verified_step < :matched`) that MUST roll the transaction back and report a replay when it changes zero rows, so a code already consumed is refused and no partial write survives. First-run password setup MUST create the bootstrap account, or re-arm an existing bootstrap one that is **not an active account holding a password**, only when no active account can already sign in; that decision MUST be taken from `dashboard_users` alone.

#### Scenario: A credential write touches one row

- **WHEN** the bootstrap account changes its password or sets a TOTP secret
- **THEN** the account row carries the new value and no legacy credential mirror is written
- **AND** a password write also clears `bootstrap_token_encrypted` and `bootstrap_token_hash` in `dashboard_settings` in the same transaction, because the first-run token is inert once any account holds a password
- **AND** the same write on an account with any other name behaves identically

#### Scenario: A replayed TOTP code is still refused

- **GIVEN** the account's replay counter is already at the step a submitted code matches
- **WHEN** the code is verified again
- **THEN** the call reports a replay, the counter does not move, and the transaction is rolled back

#### Scenario: Setup refused while an account can sign in

- **GIVEN** an active account holds a password
- **WHEN** `POST /api/dashboard-auth/password/setup` is called
- **THEN** the response is `409 password_already_configured` and no account is created

#### Scenario: A renamed install can still be re-bootstrapped

- **GIVEN** the bootstrap account was renamed and its password was then removed
- **WHEN** `POST /api/dashboard-auth/password/setup` succeeds again
- **THEN** the same row is re-armed under its current name and exactly one account exists

### Requirement: Editing an account enforces the account invariants

`PATCH /api/dashboard-users/{id}` `{username?, roleId?, displayName?, email?, status?: "active"|"disabled", force?: boolean}` SHALL apply these rules in this order: a role or status change on the caller's own account → `409 self_modification_forbidden`; a status change on an `invited` account → `409 invite_pending`; a role change on an account whose `role_source` is not `manual` (an account whose role a sign-in provider manages) → `409 role_managed_externally` unless the body carries `force: true`, which applies the change, flips `role_source` to `manual` so no later re-evaluation moves it again, and audits `role_source_overridden` (details: the provider and the previous `role_source`) next to `user_role_changed`; `force: true` without a role change → `422`, and `force: true` on an account that is already `manual` is accepted and changes nothing beyond the role; a role change → `assert_can_delegate` on the new role; any change on another account → `assert_can_act_on` on the account's current role (`403 insufficient_delegation`); a change that would leave zero active accounts holding the admin *preset* → `409 last_admin_protected` (custom roles never count); `status=active` on an account with no password and no identity → `409 credential_required`; `status=invited` → `422`. A `username` change SHALL be normalized and validated exactly as account creation is, SHALL be refused with `422 validation_error` when the requested name is reserved and with `409 username_taken` when another account holds it, MAY target the caller's own account (it is not a role or status change), MUST NOT change `session_generation`, and MUST audit `user_renamed` with the previous and the new name; the account the install bootstrapped is renameable like any other, and its deterministic id — not its name — remains the handle every other path uses to find it. The last-admin rule MUST be part of the write: the role/status UPDATE of an account that currently counts as an active admin MUST be conditional on another active admin-preset account existing at that moment, and a refused write MUST roll back the whole transaction including the key cascade. Concurrent account mutations MUST be serialised (SQLite: write-intent transaction; PostgreSQL: the active admin rows are locked `FOR UPDATE` first) so two admins disabling each other end with exactly one success. A role change and a disable MUST increment the account's `session_generation`. `status=disabled` MUST, in the same transaction, set every active key with `owner_user_id == id` to `is_active=false, deactivated_reason='owner_disabled'` and MUST invalidate the API-key cache for those keys; `status=active` MUST NOT restore keys. E-mail uniqueness applies (`409 email_taken`, also when a concurrent writer wins the race after the pre-check). The response SHALL use the listing shape, including `pendingInvite` for an invited account. Audit: `user_role_changed` (details `from`/`to` slugs), `user_disabled` plus `user_keys_deactivated` (details `count`), `user_enabled`, `user_updated`, `user_renamed`.

#### Scenario: Concurrent cross-disables keep one admin

- **GIVEN** two active admins
- **WHEN** each disables the other at the same time
- **THEN** exactly one request answers `200`, the other `409 last_admin_protected`, and one active admin remains

#### Scenario: The migrated admin is an ordinary account

- **GIVEN** a second active admin exists
- **WHEN** a caller changes the migrated `admin` account's role or status
- **THEN** the change is applied under the same rules as any other account and no refusal mentions a compatibility lock

#### Scenario: The bootstrap account is renamed

- **GIVEN** an install whose bootstrap account is still named `admin`
- **WHEN** an admin sends `{"username": "alice"}` for that account
- **THEN** the response is `200`, the account signs in under the new name, its id is unchanged, its sessions survive, and `user_renamed` is audited

#### Scenario: A rename cannot take a name that is taken or reserved

- **WHEN** an admin renames an account to a name another account holds, or back to the reserved `admin`
- **THEN** the answers are `409 username_taken` and `422 validation_error`, and neither account changes

#### Scenario: Role change ends the target's sessions

- **GIVEN** an operator is signed in
- **WHEN** an admin changes the operator's role to viewer
- **THEN** the operator's next request answers `401` and an audit row `user_role_changed` records `from: operator, to: viewer`

#### Scenario: Self and last-admin protection

- **WHEN** an admin changes their own role or disables themselves
- **THEN** the response is `409 self_modification_forbidden`
- **AND** when another account with `users:manage` disables or demotes the only active admin the response is `409 last_admin_protected`

#### Scenario: Disable cascades owned keys with a reason

- **GIVEN** an account owns an active key and a key that was deactivated manually
- **WHEN** the account is disabled
- **THEN** the active key becomes inactive with `deactivated_reason = owner_disabled`, the manual key keeps its reason, and the key cache entry is dropped

#### Scenario: An externally managed role is not edited by accident

- **GIVEN** an account provisioned by a sign-in provider (`role_source=mapping`)
- **WHEN** an admin changes its role without `force`
- **THEN** the response is `409 role_managed_externally` and the account is unchanged

#### Scenario: Taking a managed account over by hand

- **GIVEN** the same account and a provider rule that would keep it a viewer
- **WHEN** an admin repeats the change with `force: true`
- **THEN** the role changes, `role_source` becomes `manual`, `user_role_changed` and `role_source_overridden` are audited
- **AND** the next sign-in of that identity leaves the new role alone

### Requirement: Deleting an account

`DELETE /api/dashboard-users/{id}` SHALL refuse the caller's own account with `409 self_modification_forbidden` and the last active admin-preset account with `409 last_admin_protected`, then apply `assert_can_act_on`. No account SHALL be exempt from deletion for compatibility reasons; the account the install bootstrapped is deletable under exactly these rules. The DELETE of an account that counts as an active admin MUST be conditional on another active admin existing at that moment (a refused write rolls the whole transaction back). On success it SHALL deactivate the account's active keys with `deactivated_reason='owner_disabled'`, clear their `owner_user_id`, delete the account with its identities and invite, answer `204`, and audit `user_deleted` keeping the username as text.

#### Scenario: The migrated admin can be deleted

- **GIVEN** a second active admin exists and local sign-in is not restricted
- **WHEN** an admin deletes the migrated `admin` account
- **THEN** the response is `204` and the account is gone

#### Scenario: The last admin is still protected

- **GIVEN** the migrated `admin` account is the only active admin
- **WHEN** a caller with `users:manage` deletes it
- **THEN** the response is `409 last_admin_protected` and the account survives

#### Scenario: Keys survive their owner, inactive

- **GIVEN** an operator owns an active key
- **WHEN** an admin deletes the operator
- **THEN** the key row remains with `owner_user_id` NULL, `is_active` false, `deactivated_reason = owner_disabled`, and the operator's sessions are refused

### Requirement: Account actions

`POST /api/dashboard-users/{id}/reset-totp` SHALL refuse the caller's own account (`409 self_modification_forbidden`; self-service uses `/totp/disable`), SHALL apply `assert_can_act_on`, SHALL clear the account's TOTP secret and replay counter WITHOUT changing `totp_required_on_login`, increment its `session_generation`, and audit `user_totp_reset`; no account SHALL be exempt for compatibility reasons, and an account reset while a TOTP requirement binds it is held at the enrolment gate on its next request like any other. `POST /api/dashboard-users/{id}/revoke-sessions` SHALL apply `assert_can_act_on`, increment the generation, and audit `user_sessions_revoked` with `scope: admin`. `POST /api/dashboard-users/{id}/reactivate-keys` SHALL require the account to be `active` (`409 user_not_active`), apply `assert_can_act_on`, and set every key with `owner_user_id == id` and `deactivated_reason='owner_disabled'` back to active with the reason cleared by an UPDATE that is serialised with account mutations and conditional on the owner being `active` at that moment (a disable that commits concurrently leaves the keys inactive and the call answers `409 user_not_active`), invalidate their cache entries, answer `{reactivated: n}`, and audit `user_keys_reactivated` (details `count`).

#### Scenario: Administrative TOTP reset keeps the install policy

- **GIVEN** `totp_required_on_login` is on and two admins are enrolled
- **WHEN** one admin resets the other's TOTP
- **THEN** the flag stays on and the other admin's next login is held at `totp_enrollment_required`
- **AND** resetting the migrated `admin` account answers `200` under the same rule

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

## ADDED Requirements

### Requirement: Legacy dashboard credential columns are dropped

One Alembic revision parented on the single head (`20260912_000000_merge_thread_cache_and_bridge_retirement_heads`) MUST drop `dashboard_settings.password_hash`, `dashboard_settings.totp_secret_encrypted` and `dashboard_settings.totp_last_verified_step`, and MUST leave the graph with exactly one head. It MUST NOT touch `guest_password_hash`, `guest_session_generation`, `bootstrap_token_encrypted`, `bootstrap_token_hash`, `totp_required_on_login`, `totp_required_for_admin_role` or `local_login_policy`, all of which remain live. The revision MUST use `op.batch_alter_table` so SQLite is supported, MUST guard each column with an inspector check in **both** directions so re-running either body converges, and MUST NOT import anything from `app.*`. The downgrade MUST re-add the three columns as nullable **and** re-project them from the `dashboard_users` row carrying the deterministic compat id (frozen as a literal in the revision), because after this release the bootstrap account's username is no longer a stable way to find it; when that row is absent the columns MUST be left NULL. `DashboardSettings` MUST stop mapping the three columns in the same change, and `SettingsRepository.get_or_create()` MUST stop seeding them.

Re-applying the chain over a database that has already dropped the columns — a lost ledger, a rewound one, or an existing schema bootstrapped without one — MUST NOT damage the account rows, and MUST NOT be defended by editing an already-published revision. `20260909_010000_add_dashboard_users` and `20260909_020000_reproject_compat_admin_credentials` are ancestors of `main` and SHALL stay byte-identical apart from comments: an install that has already applied a revision never applies it again, so a guard added inside one is dead for exactly the installs it was added for, and changing what a shipped revision id does leaves one id with two histories. The defence therefore belongs to `run_upgrade()`, which reaches the replay before the first revision does. To make that possible the drop revision MUST record a durable marker — a `runtime_sentinels` row naming the revision that wrote it, removed again by its downgrade — stating that the legacy credential layer is retired. The marker MUST be data rather than ledger, precisely so that it survives whatever removed the ledger.

#### Scenario: The drop revision leaves a durable retirement marker

- **GIVEN** a database being upgraded across the drop revision
- **WHEN** the revision has run
- **THEN** `runtime_sentinels` carries a row naming the drop revision, and downgrading the revision removes that row again

#### Scenario: Upgrade drops exactly three columns

- **GIVEN** a database at the parent revision with the three legacy credential columns populated
- **WHEN** the revision is applied
- **THEN** `dashboard_settings` no longer has `password_hash`, `totp_secret_encrypted` or `totp_last_verified_step`
- **AND** the guest columns, the bootstrap-token columns, both TOTP requirement flags and `local_login_policy` are unchanged

#### Scenario: Downgrade re-projects from the account row

- **GIVEN** the schema is at the drop revision and the bootstrap account has been renamed and holds a password and a TOTP secret
- **WHEN** the revision is downgraded
- **THEN** the three columns exist again and carry that account's password hash, secret and replay step, found by its deterministic id rather than by its name

#### Scenario: Both bodies are idempotent

- **WHEN** the upgrade body runs against a database whose columns are already gone, or the downgrade body against one where they are already present
- **THEN** each completes without error and converges on the required schema and credential projection: the upgrade leaves the three columns absent, and the downgrade leaves them present and holding the compat account row's values, re-projecting them even when the columns were already there

### Requirement: Credential paths write install-wide settings without keying on an account name

The install-wide settings that credential paths write SHALL be decided by the operation, never by the acting account's username. Setting or changing any account's password MUST clear `bootstrap_token_encrypted` and `bootstrap_token_hash` in the same transaction, so a live remote bootstrap token cannot outlive the credential it was meant to create. Removing the dashboard password (`DELETE /api/dashboard-auth/password`, already restricted to a single-account install) MUST additionally clear both `totp_required_on_login` and `totp_required_for_admin_role`, so the install cannot be left requiring a factor no account can present. Self-service TOTP disable MUST clear `totp_required_on_login` **only** when the acting account is the install's only account; on any other install it MUST leave the requirement alone, because `/totp/disable` carries no `security:write` and an install-wide setting MUST NOT be movable from a self-service route. "Only account" SHALL count every account the install holds in any status, not only the active ones: a disabled account keeps its role and can be enabled again by anybody holding `users:manage`, so treating it as absent would let a self-service route decide somebody else's sign-in. Administrative `reset-totp` MUST never change either requirement.

#### Scenario: Password removal leaves no unreachable requirement

- **GIVEN** a single-account install with a password, a TOTP secret and `totp_required_on_login` on
- **WHEN** the account removes the dashboard password
- **THEN** both TOTP requirements are off, the bootstrap token is cleared and re-issued by the normal passwordless path, and the install requires no sign-in

#### Scenario: A team member cannot turn off the install requirement

- **GIVEN** an install with two active accounts and `totp_required_on_login` on
- **WHEN** one of them disables its own TOTP through `/totp/disable`
- **THEN** the requirement stays on and that account is held at the enrolment gate on its next request

#### Scenario: A disabled colleague is still an account

- **GIVEN** an install with one active account, one disabled account and `totp_required_on_login` on
- **WHEN** the active account disables its own TOTP through `/totp/disable`
- **THEN** the requirement stays on
- **AND** after the disabled account is deleted the same call turns it off

#### Scenario: The rules do not depend on the name

- **GIVEN** the bootstrap account has been renamed
- **WHEN** it changes its password, and later removes it on a single-account install
- **THEN** the bootstrap token is cleared on the change and both requirements are cleared on the removal, exactly as before the rename

### Requirement: Password removal counts every account the install holds

`DELETE /api/dashboard-auth/password` SHALL refuse with `409 other_users_exist` unless the acting account is the **only** account the install holds, counted in every status and not only among the active ones (live invites and the account's own external identities already refuse it). Now that any account may be disabled — including the one the install bootstrapped — a removal that ignored disabled rows would return the install to the passwordless bootstrap state while they survive: local requests are then served as the implicit admin, which holds no account and therefore cannot enable, delete or act as anybody, so a disabled row can neither sign in (`disabled_user`) nor be brought back, and it would return exempt from the two install-wide TOTP requirements the removal clears on the grounds that the acting account *is* the install. The refusal message SHALL name deleting the other accounts as the way through. Accounts whose invite is no longer live (awaiting the lazy purge) SHALL NOT count.

#### Scenario: A disabled account blocks the removal

- **GIVEN** an install with one active admin holding a password and one disabled account
- **WHEN** the active admin calls `DELETE /api/dashboard-auth/password`
- **THEN** the response is `409 other_users_exist` and sign-in is still required
- **AND** after the disabled account is deleted the same call answers `200`
