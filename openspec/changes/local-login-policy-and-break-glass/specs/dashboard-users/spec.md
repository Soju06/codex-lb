## ADDED Requirements

### Requirement: The break-glass invariant is one guard called from every mutation

There SHALL be exactly one service function that decides whether a change may proceed, and every path that can remove the last emergency way in SHALL call it. The guard is **bidirectional and post-state**: it computes the state the write would produce — role, status, designation, second factor and local password after the change — and refuses with `409 last_break_glass_protected` when that state would leave zero qualifying break-glass accounts while `local_login_policy` is not `enabled`. Refusal is not the same as the last-admin rule: an install may hold several admins and still have exactly one of them qualifying. While the policy is `enabled` the guard SHALL be a no-op, because local sign-in is open to everybody and no account is the only way in. Like the last-admin invariant, the guard MUST be part of the write, not only a pre-check: the mutation SHALL be serialised with the existing write-intent transaction (SQLite) or `FOR UPDATE` over the candidate rows (PostgreSQL) and the conditional write SHALL re-apply the count, so two administrators removing the last two second factors at the same time end with exactly one success. A refused write SHALL roll back its whole transaction, including any key cascade, and SHALL leave no audit row for the change it did not make.

The call sites SHALL be, exhaustively: the role change and the status change of `PATCH /api/dashboard-users/{id}`, `DELETE /api/dashboard-users/{id}`, clearing `isBreakGlass` on `PATCH /api/dashboard-users/{id}`, the self-service `POST /api/dashboard-auth/totp/disable`, the self-service `DELETE /api/dashboard-auth/password`, the administrative `POST /api/dashboard-users/{id}/reset-totp`, the shared `deactivate_user()` back-channel, and the custom-role delete-and-reassign transaction when it ships. Identity-mapping re-evaluation SHALL NOT call it and SHALL NOT need to: a break-glass account is `role_source=manual`, which re-evaluation never touches.

Serialisation is a property of the *sequence*, not only of the call. A path SHALL acquire the account write intent **before its first read** — `BEGIN IMMEDIATE` cannot run inside an open transaction, so a late acquisition takes the lock only through a fallback statement — and it SHALL NOT commit between the count and the write, because a commit releases the lock. Where a path must commit something else first (advancing the TOTP replay counter, for instance) that commit SHALL happen before the guard runs, not between the guard and the write.

#### Scenario: Removing the last emergency password

- **GIVEN** `local_login_policy` is `break_glass_only` and the only qualifying account is the caller
- **WHEN** it submits `DELETE /api/dashboard-auth/password` with its correct password
- **THEN** the response is `409 last_break_glass_protected` and the password is unchanged

#### Scenario: Two administrators racing the administrative reset

- **GIVEN** `local_login_policy` is `admins_only` and exactly two qualifying accounts exist
- **WHEN** two `reset-totp` requests for the two accounts are processed concurrently
- **THEN** exactly one succeeds and one qualifying account remains

#### Scenario: Demoting the last qualifying account

- **GIVEN** `local_login_policy` is `admins_only` and exactly one qualifying break-glass account exists
- **WHEN** an admin changes that account's role to operator
- **THEN** the response is `409 last_break_glass_protected` and the account keeps its role

#### Scenario: Disabling the last qualifying account

- **WHEN** the same account's `status` is set to `disabled`
- **THEN** the response is `409 last_break_glass_protected`, the account stays active, and none of its keys are cascaded

#### Scenario: Deleting the last qualifying account

- **WHEN** `DELETE /api/dashboard-users/{id}` names that account
- **THEN** the response is `409 last_break_glass_protected` and the row still exists

#### Scenario: Clearing the designation on the last qualifying account

- **WHEN** an admin patches that account with `isBreakGlass: false`
- **THEN** the response is `409 last_break_glass_protected` and the designation is unchanged

#### Scenario: Self-service removal of the last second factor

- **WHEN** that account posts a valid code to `/api/dashboard-auth/totp/disable`
- **THEN** the response is `409 last_break_glass_protected` and the secret is unchanged

#### Scenario: Administrative reset of the last second factor

- **GIVEN** a second, non-compat admin holds `users:manage`
- **WHEN** it posts `/api/dashboard-users/{id}/reset-totp` for the last qualifying account
- **THEN** the response is `409 last_break_glass_protected` and the secret is unchanged

#### Scenario: The shared deactivation back-channel refuses too

- **WHEN** `deactivate_user()` is called for the last qualifying account by any caller
- **THEN** it refuses with `409 last_break_glass_protected`, the account stays active, and an audit row `scim_deprovision_refused` records the attempt

#### Scenario: Custom-role deletion with reassignment

- **GIVEN** a custom role whose delete-and-reassign would move the last qualifying account off the admin preset
- **WHEN** the reassignment is submitted
- **THEN** the whole transaction is refused with `409 last_break_glass_protected` and no account is reassigned

#### Scenario: Mapping re-evaluation is exempt by construction

- **GIVEN** a provider rule that would demote everyone it does not match
- **WHEN** the resolver re-evaluates on the next sign-in
- **THEN** the break-glass account is skipped because its `role_source` is `manual`, and the guard is never consulted

#### Scenario: The guard sleeps while local sign-in is open

- **GIVEN** `local_login_policy` is `enabled` and exactly one qualifying break-glass account exists
- **WHEN** an admin disables, demotes, deletes it or removes its second factor
- **THEN** each change is applied, subject only to the ordinary account invariants

#### Scenario: Two administrators race the last two second factors

- **GIVEN** `local_login_policy` is `admins_only` and exactly two qualifying break-glass accounts exist
- **WHEN** two admins reset one second factor each at the same time
- **THEN** exactly one request answers `200`, the other `409 last_break_glass_protected`, and one qualifying account remains

### Requirement: The break-glass designation is editable and manual

`PATCH /api/dashboard-users/{id}` SHALL accept `isBreakGlass` in addition to the fields it already takes. Setting it SHALL require the target to hold the admin preset (`422` otherwise) and SHALL be a `users:manage` change like any other, so delegation, step-up and the compat-account lock all apply first. An account carrying the designation SHALL always be `role_source=manual`, so no sign-in provider re-evaluation can move it; setting the designation on an account whose role a provider manages SHALL flip `role_source` to `manual` and audit `role_source_overridden` next to `user_updated`. Clearing it SHALL run the shared break-glass guard. The listing response already reports `isBreakGlass`, so the flag is readable everywhere it is writable.

#### Scenario: Designating a second emergency account

- **GIVEN** one qualifying break-glass account and a second admin holding a TOTP secret
- **WHEN** an admin patches the second admin with `isBreakGlass: true`
- **THEN** the response is `200`, the account's `role_source` is `manual`, and the qualifying count is two

#### Scenario: Clearing is allowed once another account qualifies

- **WHEN** the first account is then patched with `isBreakGlass: false`
- **THEN** the response is `200` and the designation is cleared

### Requirement: Accounts are deactivated through one shared function

Deactivating an account SHALL go through one `deactivate_user()` function that owns the whole effect — status, session generation, the owned-key cascade, the audit row — so every caller behaves the same and none can bypass the break-glass guard. In this release its callers are the status change of the user PATCH and the identity resolver's demotion path; the SCIM `active=false` endpoint joins them in Phase 3b, where a refusal MUST surface as a SCIM `409` and MUST audit `scim_deprovision_refused`. Because the resolver's path runs on a request-serving code path and would otherwise raise on every cache TTL, a refusal there SHALL pin the account to `role_source=manual` and audit once, exactly as the last-admin rule already does, instead of raising repeatedly.

#### Scenario: One implementation, one behaviour

- **GIVEN** the last qualifying break-glass account while `local_login_policy` is `break_glass_only`
- **WHEN** it is deactivated through the user PATCH and through the shared function directly
- **THEN** both refuse with `409 last_break_glass_protected` and neither cascades any key

#### Scenario: The resolver does not spin on a refusal

- **GIVEN** a provider rule whose no-match role is null, so it would disable a break-glass account
- **WHEN** the account signs in twice across the identity-cache window
- **THEN** the account stays active, its `role_source` is `manual`, and exactly one refusal is audited
