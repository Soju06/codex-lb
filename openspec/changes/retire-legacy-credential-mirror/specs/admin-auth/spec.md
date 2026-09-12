## MODIFIED Requirements

### Requirement: Dashboard authentication resolves against user accounts

The system SHALL decide whether a dashboard install requires sign-in, who a session belongs to, and what that session may do from `dashboard_users`. The schema SHALL carry no second copy of a dashboard credential: `dashboard_settings` no longer holds `password_hash`, `totp_secret_encrypted` or `totp_last_verified_step`, and nothing MAY re-derive sign-in state from a settings column. The derived local auth state MUST expose: whether any account exists, the number of active accounts, the number of active accounts holding a password, whether sign-in is required (an active account holds a password or an external identity), and the id of the sole active password-holding account when exactly one exists. The state MUST be served from a per-process cache with a time-to-live of at most 5 seconds that is dropped whenever the `dashboard_users` cache-invalidation namespace is bumped, and every user write MUST bump that namespace. An install whose state does not require sign-in MUST keep today's behaviour: local requests act as the implicit admin (`auth_method` `local_bootstrap`) and remote requests require the bootstrap token or guest access.

#### Scenario: Passwordless install is unchanged

- **GIVEN** no active account holds a password or identity
- **WHEN** a local request reaches a dashboard route
- **THEN** it is served as the implicit admin with no user id
- **AND** a remote request without guest access is refused with `bootstrap_required`

#### Scenario: There is no legacy credential to consult

- **GIVEN** a database at head
- **WHEN** the schema is inspected and the session dependency evaluates a request
- **THEN** `dashboard_settings` carries none of the three legacy credential columns and the decision is taken from the accounts alone

### Requirement: First-run setup is compare-and-set

`POST /api/dashboard-auth/password/setup` MUST write the first credential with a compare-and-set, and MUST identify the row it may re-arm by that row's deterministic compat id and break-glass designation rather than by its username, so an account renamed after bootstrap is still the account setup re-arms: a missing row is inserted (a concurrent insert fails on the unique id and username), an existing bootstrap row is re-armed only while it is **not an active account holding a password** — the same fact the refusal above tests, re-read inside the statement — and when zero rows change the request MUST answer `409 password_already_configured` and MUST NOT touch the bootstrap token. The condition MUST NOT be narrowed to `password_hash IS NULL`: a disabled bootstrap row still holds its hash, so such a row would never match again and the install could neither sign in (the local form refuses every non-active account) nor ever set a password — with no in-product way back. Because the row setup hands back is the install's way in, the same statement MUST restore the state setup promises: `active`, the admin preset, and a manually sourced role. Setup is refused only while an active account holds a password: accounts that sign in through an external identity alone do not count, so a reverse-proxy install can still create the local break-glass password account. Exactly one of any set of concurrent setups succeeds and only its password signs in. The request body carries no username and the bootstrap screen offers no username field; the account is created as `admin` and may be renamed afterwards through account management.

#### Scenario: Concurrent setups admit exactly one password

- **WHEN** two `POST /api/dashboard-auth/password/setup` requests race
- **THEN** one answers `200` and the other `409 password_already_configured`
- **AND** only the password of the `200` response signs in

#### Scenario: Proxy accounts do not block the break-glass admin

- **GIVEN** a trusted-header install whose only accounts were created from proxy identities
- **WHEN** a proxy-authenticated admin runs the password setup
- **THEN** the `admin` account is created with that password and signs in without the header

#### Scenario: Re-bootstrap after a rename

- **GIVEN** the bootstrap account was renamed to `alice` and `DELETE /api/dashboard-auth/password` left its credential-less row in place
- **WHEN** `POST /api/dashboard-auth/password/setup` is called
- **THEN** the response is `200`, the row named `alice` holds the new password, and no second account is created

#### Scenario: Re-bootstrap after the account was disabled

- **GIVEN** a reverse-proxy install whose bootstrap account was disabled while still holding its password hash, so no active account holds a password
- **WHEN** an account that manages users runs `POST /api/dashboard-auth/password/setup`
- **THEN** the response is `200`, that same row is active, admin-preset and still designated, and it holds the new password
- **AND** the new password signs in through the local form while the one it replaced does not

### Requirement: The username admin is reserved

`admin` SHALL remain reserved for the local break-glass account even though that account may be renamed away from it: `POST /api/dashboard-users`, the username edit of `POST /api/dashboard-auth/invite/accept` and a `username` change through `PATCH /api/dashboard-users/{id}` MUST refuse the name with `422 validation_error`, and the just-in-time username derived for an external identity MUST skip it. The rename is therefore one-way — the bootstrap account may leave the name and MUST NOT be able to return to it, and no other account may take it — so the name cannot come to mean a different account than the recovery documentation says it does. The reservation SHALL be a rule about the *name* only: no refusal, gate or credential path MAY identify the bootstrap account by it, and first-run setup MUST re-arm on the row's deterministic compat id and break-glass designation instead, answering `409 password_already_configured` for any other row.

#### Scenario: Reserved name

- **WHEN** an admin creates an account named `Admin`, an invitee renames themselves to `admin`, or an admin renames an account to `admin`
- **THEN** all three answer `422`, and a stray non-break-glass row named `admin` is never re-armed by setup

#### Scenario: A proxy identity called admin still gets its own name

- **GIVEN** a trusted-header install
- **WHEN** the header first carries `admin`
- **THEN** the account created for that identity is named `admin-2` and the bootstrap account is untouched

### Requirement: Enabling a TOTP requirement needs the acting account's own secret

Turning `totp_required_on_login` or `totp_required_for_admin_role` from off to on through `PUT /api/settings` MUST be refused with `400 invalid_totp_config` unless the acting account holds a TOTP secret; the guard MUST read the acting account's secret (the user row) and MUST fire only on the off→on transition so that saving other settings while a requirement is on is not refused for accounts without a secret. No further condition SHALL gate either transition: the state of any other account, including the account the install bootstrapped, MUST NOT refuse it, and the settings response MUST NOT report a per-account compatibility fact. The settings response SHALL NOT carry a per-account `totpConfigured` (it lives in the session response, which is not cached across accounts); it SHALL report `usersWithoutTotpCount` as the number of active password accounts without a secret and `adminsWithoutTotpCount` as the subset of those that are admin-level. `totp_required_for_admin_role` SHALL be a security field of `PUT /api/settings` and SHALL appear in the `settings_changed` audit details when its value changes. Removing the dashboard password on a single-account install (`DELETE /api/dashboard-auth/password`) SHALL reset both requirements, so the next bootstrap starts with neither.

#### Scenario: Actor without a secret cannot enable either requirement

- **GIVEN** a signed-in admin without a TOTP secret
- **WHEN** it sends `PUT /api/settings` with `totpRequiredForAdminRole: true`, or with `totpRequiredOnLogin: true`
- **THEN** the response is `400 invalid_totp_config` and the setting is unchanged

#### Scenario: An unenrolled migrated admin no longer blocks the requirement

- **GIVEN** the migrated `admin` account has a password and no TOTP secret, and a second admin has enrolled
- **WHEN** the second admin sends `PUT /api/settings` with `totpRequiredOnLogin: true`
- **THEN** the response is `200`, the requirement is on, and the migrated account is held at the enrolment gate at its next sign-in
- **AND** no response of the settings API reports a compatibility state for that account

#### Scenario: Password removal clears both requirements

- **GIVEN** a single-account install with `totp_required_for_admin_role` on
- **WHEN** the admin removes the dashboard password and a new password is set up
- **THEN** the new admin reads `/api/settings` with `200` and both requirements are off

#### Scenario: Counts and enabling after enrolment

- **GIVEN** two admin-preset accounts, one operator, one viewer and one account with a custom role granting `audit:read`, none with a secret
- **WHEN** the first admin reads `/api/settings`
- **THEN** `usersWithoutTotpCount` is 5 and `adminsWithoutTotpCount` is 3
- **WHEN** that admin enrols and sends `PUT /api/settings` with `totpRequiredForAdminRole: true`
- **THEN** the response is `200` with `totpRequiredForAdminRole: true`, `usersWithoutTotpCount` 4 and `adminsWithoutTotpCount` 2
- **AND** an operator's `PUT /api/settings` changing only `stickyThreadsEnabled` still succeeds

## ADDED Requirements

### Requirement: Host password recovery says when the account it reset still cannot sign in

Every account can now be disabled, including the one the install bootstrapped, and a password is a way in only for an `active` account: the local form refuses every other status before it looks at a hash. `codex-lb admin reset-password` SHALL therefore report the target's status in terms of that consequence, naming re-enabling by an administrator as what is still missing, whenever the account it just wrote to is not `active`. The command SHALL NOT change the status itself — turning an account an administrator disabled back on is an administrator's decision, not a recovery command's — and SHALL still perform the write, the session revocation and the audit exactly as it does for an active account.

#### Scenario: Resetting the password of a disabled account

- **GIVEN** an account that an administrator disabled
- **WHEN** the operator runs `codex-lb admin reset-password <that account>`
- **THEN** the stored hash changes and the report says the account cannot sign in until an administrator re-enables it
- **AND** the account's status is unchanged
