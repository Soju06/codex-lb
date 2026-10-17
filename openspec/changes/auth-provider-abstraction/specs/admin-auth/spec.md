## ADDED Requirements

### Requirement: Trusted-header requests resolve to accounts

When `dashboard_auth_mode` is `trusted_header` and the request carries the singular trusted identity header, the session dependency SHALL resolve the identity through the identity resolver and serve the request as a **user principal** of the resolved account (its role's grants, `auth_method=trusted_header`, `auth_mode=trusted_header`, audit actor = the account) instead of an implicit admin. A role without `dashboard:read` at scope `all` is refused with `403 permission_required`. When the identity is refused (no account, or a disabled account) and the request also carries a password-verified cookie of an active account, that account SHALL be served (the break-glass admin stays reachable while the proxy asserts a stranger); a resolved header account always wins over a cookie. Otherwise a refused identity SHALL answer `401 identity_not_provisioned` and a disabled account `401 account_disabled`. A header whose provider row is disabled, or whose trimmed value is empty or longer than 512 characters, SHALL be treated as absent (`401 proxy_auth_required`). Requests without the header keep today's behaviour (`401 proxy_auth_required` unless a password session is present). The TOTP policy is not applied to header sessions in this release.

#### Scenario: Header user is an account

- **WHEN** the proxy sends `alice@example.com` on a fresh trusted-header install
- **THEN** the request is served as the new account `alice.example.com` and audit rows written by that request carry its user id

#### Scenario: Refused identity

- **GIVEN** the trusted-header provider refuses unknown identities
- **WHEN** an unknown identity requests a protected route
- **THEN** the response is `401 identity_not_provisioned`

#### Scenario: Break-glass cookie behind a refused identity

- **GIVEN** the provider refuses unknown identities and the caller holds the `admin` password session
- **WHEN** the proxy asserts a stranger on a protected route
- **THEN** the request is served as `admin`, while the same request without the cookie answers `401 identity_not_provisioned`
- **AND** a request whose header resolves to an account is served as that account even with the cookie

### Requirement: Break-glass setup behind the proxy needs a managing account

In `trusted_header` mode `POST /api/dashboard-auth/password/setup` SHALL apply the ordinary session dependency and require `users:manage`: a bare request answers `401 proxy_auth_required`, a refused identity `401 identity_not_provisioned`, a resolved account without `users:manage` `403 permission_required`; only then is the local `admin` created. The route MUST NOT create an account for a request that carries neither a resolved header identity nor a valid password session.

#### Scenario: Only a managing account creates the break-glass admin

- **GIVEN** proxy-created accounts exist
- **WHEN** the setup is posted without the header, with a refused identity, and with a viewer's identity
- **THEN** the answers are `401 proxy_auth_required`, `401 identity_not_provisioned` and `403 permission_required`, and no `admin` account exists
- **AND** the same request from an admin's identity answers `200`

### Requirement: The username admin is reserved

`admin` SHALL be reserved for the local break-glass account: `POST /api/dashboard-users` and the username edit of `POST /api/dashboard-auth/invite/accept` MUST refuse it with `422 validation_error`, and first-run setup MUST re-arm an existing credential-less row only when it is the bootstrapped break-glass row (`is_break_glass` with the compat id), answering `409 password_already_configured` otherwise.

#### Scenario: Reserved name

- **WHEN** an admin creates an account named `Admin` or an invitee renames themselves to `admin`
- **THEN** both answer `422`, and a stray non-break-glass row named `admin` is never re-armed by setup

## MODIFIED Requirements

### Requirement: Dashboard guest access is read-only

The system SHALL support a dashboard `guest` role with read permission and without write permission. The system SHALL continue to treat password-authenticated, disabled-auth, and local bootstrap users as `admin` principals with read and write permissions. A trusted-header request is served as the account its identity resolves to, with that account's grants. Guest read permission covers only guest-safe dashboard data: full conversation archives and request-log filtering by a dedicated conversation identifier MUST require an `admin` principal.

#### Scenario: Guest can read dashboard APIs

- **WHEN** guest access is enabled and a guest principal requests a guest-safe dashboard GET endpoint
- **THEN** the request succeeds using read-only dashboard access
- **AND** the session response identifies the principal as `guest`
- **AND** the session response includes only the `read` permission

#### Scenario: Guest cannot mutate dashboard state

- **WHEN** guest access is enabled and a guest principal requests a dashboard mutating endpoint
- **THEN** the system returns HTTP 403 with error code `read_only_access`
- **AND** no dashboard state is changed

#### Scenario: Guest cannot read conversation archives

- **WHEN** a guest principal requests any conversation-archive endpoint
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no archive file metadata, payload, headers, or other archive record data is returned

#### Scenario: Guest cannot filter request logs by conversation identifier

- **WHEN** a guest principal requests request logs with the dedicated `conversation_id` filter
- **THEN** the system returns HTTP 403 with error code `admin_access_required`
- **AND** no filtered rows, request count, or aggregated conversation cost is returned

### Requirement: Trusted-header identity evidence is singular

The system MUST create a trusted-header dashboard principal only when a trusted raw proxy peer supplies exactly one occurrence of the configured identity field and its trimmed value is non-empty. The system MUST treat two or more occurrences as ambiguous regardless of field-name casing, field order, value equality, or whether another occurrence is empty. Ambiguous identity evidence MUST NOT produce an authenticated principal or actor.

#### Scenario: Duplicate trusted identity fields are rejected

- **WHEN** a trusted raw proxy peer sends two or more occurrences of the configured dashboard identity field
- **THEN** a protected dashboard request returns HTTP 401 with error code `proxy_auth_required`
- **AND** no trusted-header principal or actor is produced

#### Scenario: One non-empty trusted identity field authenticates

- **WHEN** a trusted raw proxy peer sends exactly one configured dashboard identity field with a non-empty trimmed value
- **THEN** the system resolves that trimmed value through the identity resolver and serves the request as the resolved account

### Requirement: Session response describes the account and the login screen

`GET /api/dashboard-auth/session` (and the session-issuing endpoints) SHALL add the optional fields `user` (`{id, username, display_name, role: {id, slug, name, kind}}` or null), `auth_method`, `must_change_password`, `totp_enrollment_required`, `login`, `access_summary`, `assignable_role_ids`, and `local_password_configured` (true exactly when an active account holds a password; accounts that sign in through a provider alone do not count, so the Password card follows it rather than `password_required`). `login` MUST be present for every caller, MUST never contain a username, MUST set `username_field` to `hidden` exactly when one active account holds a password and to `shown` otherwise, MUST list the active providers in `providers` (`{kind, provider_key, label, login_url}`; `password` first, `trusted_header` when active, `login_url` null), and MUST set `pending_identity` to true exactly when the request carried a provider identity that resolved to no active account. `access_summary` (`users_total`, `users_active`, `users_invited`, `users_disabled`, `pending_invites`, `non_admin_users`, `custom_roles`, `providers_enabled` = the active provider kinds, `role_mappings`, `scim_tokens`, `audit_sinks`, `local_login_policy`) and a non-empty `assignable_role_ids` MUST be returned only to an authenticated principal holding `users:manage`; every other caller MUST receive `null` and `[]`. `assignable_role_ids` MUST list exactly the `admin`, `operator`, and `viewer` presets in this release (`member` becomes assignable when own-scoped views ship; `guest` never). `permissions` MUST keep the `read`/`write` aliases and additionally list every grant as `<permission>:<scope>`. `role` MUST remain `admin` or `guest`. `password_required` MUST equal the derived "sign-in required" state, except that a header-less request in `trusted_header` mode while no active account holds a password reports `password_required=false` and `authenticated=false` (the client shows the reverse-proxy notice, not a login form). A trusted-header request resolved to an account SHALL be described like that account's session (`authenticated=true`, `password_required=true`, `user`, `auth_method=trusted_header`, the account's permissions, team facts when it holds `users:manage`); `password_session_active` reports whether a fallback password cookie also rode along. A refused identity accompanied by a password-verified cookie SHALL be described as that cookie's session (no `pending_identity`). A header whose provider is inactive SHALL be described as unauthenticated without team facts.

#### Scenario: Signed-in admin sees the account block and the summary

- **WHEN** an admin account requests the session
- **THEN** `user.username` is the account's username, `auth_method` is `password`, `login.username_field` is `hidden`
- **AND** `access_summary.users_total` counts every account and `permissions` contains `read`, `write`, and `users:manage:all`

#### Scenario: Guest and unauthenticated callers get no team facts

- **WHEN** a guest session or an unauthenticated client requests the session
- **THEN** `access_summary` is null, `assignable_role_ids` is empty, and `user` is null
- **AND** `login` is present without any username

#### Scenario: Refused proxy identity

- **WHEN** a trusted-header request whose identity resolves to no active account asks for the session
- **THEN** `authenticated` is false, `user` is null and `login.pending_identity` is true

### Requirement: First-run setup is compare-and-set

`POST /api/dashboard-auth/password/setup` MUST write the first credential with a compare-and-set: a missing `admin` row is inserted (a concurrent insert fails on the unique username), an existing credential-less row is re-armed only where `password_hash IS NULL`; when zero rows change the request MUST answer `409 password_already_configured` and MUST NOT touch the legacy columns or the bootstrap token. Setup is refused only while an active account holds a password: accounts that sign in through an external identity alone do not count, so a reverse-proxy install can still create the local `admin` as its break-glass password fallback. Exactly one of any set of concurrent setups succeeds and only its password signs in.

#### Scenario: Concurrent setups admit exactly one password

- **WHEN** two `POST /api/dashboard-auth/password/setup` requests race
- **THEN** one answers `200` and the other `409 password_already_configured`
- **AND** only the password of the `200` response signs in

#### Scenario: Proxy accounts do not block the break-glass admin

- **GIVEN** a trusted-header install whose only accounts were created from proxy identities
- **WHEN** a proxy-authenticated admin runs the password setup
- **THEN** the `admin` account is created with that password and signs in without the header
