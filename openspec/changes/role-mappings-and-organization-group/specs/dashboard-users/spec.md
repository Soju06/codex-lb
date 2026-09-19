## MODIFIED Requirements

### Requirement: Editing an account enforces the account invariants

`PATCH /api/dashboard-users/{id}` `{roleId?, displayName?, email?, status?: "active"|"disabled", force?: boolean}` SHALL apply these rules in this order: a role or status change on the migrated compat `admin` account → `409 compat_user_locked` (display name and e-mail stay editable); a role or status change on the caller's own account → `409 self_modification_forbidden`; a status change on an `invited` account → `409 invite_pending`; a role change on an account whose `role_source` is not `manual` (an account whose role a sign-in provider manages) → `409 role_managed_externally` unless the body carries `force: true`, which applies the change, flips `role_source` to `manual` so no later re-evaluation moves it again, and audits `role_source_overridden` (details: the provider and the previous `role_source`) next to `user_role_changed`; `force: true` without a role change → `422`, and `force: true` on an account that is already `manual` is accepted and changes nothing beyond the role; a role change → `assert_can_delegate` on the new role; any change on another account → `assert_can_act_on` on the account's current role (`403 insufficient_delegation`); a change that would leave zero active accounts holding the admin *preset* → `409 last_admin_protected` (custom roles never count); `status=active` on an account with no password and no identity → `409 credential_required`; `status=invited` → `422`. The last-admin rule MUST be part of the write: the role/status UPDATE of an account that currently counts as an active admin MUST be conditional on another active admin-preset account existing at that moment, and a refused write MUST roll back the whole transaction including the key cascade. Concurrent account mutations MUST be serialised (SQLite: write-intent transaction; PostgreSQL: the active admin rows are locked `FOR UPDATE` first) so two admins disabling each other end with exactly one success. A role change and a disable MUST increment the account's `session_generation`. `status=disabled` MUST, in the same transaction, set every active key with `owner_user_id == id` to `is_active=false, deactivated_reason='owner_disabled'` and MUST invalidate the API-key cache for those keys; `status=active` MUST NOT restore keys. E-mail uniqueness applies (`409 email_taken`, also when a concurrent writer wins the race after the pre-check). The response SHALL use the listing shape, including `pendingInvite` for an invited account. Audit: `user_role_changed` (details `from`/`to` slugs), `user_disabled` plus `user_keys_deactivated` (details `count`), `user_enabled`, `user_updated`.

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

#### Scenario: An externally managed role is not edited by accident

- **GIVEN** an account provisioned by a sign-in provider (`role_source=mapping`)
- **WHEN** an admin changes its role without `force`
- **THEN** the response is `409 role_managed_externally` and the account is unchanged

#### Scenario: Taking a managed account over by hand

- **GIVEN** the same account and a provider rule that would keep it a viewer
- **WHEN** an admin repeats the change with `force: true`
- **THEN** the role changes, `role_source` becomes `manual`, `user_role_changed` and `role_source_overridden` are audited
- **AND** the next sign-in of that identity leaves the new role alone
