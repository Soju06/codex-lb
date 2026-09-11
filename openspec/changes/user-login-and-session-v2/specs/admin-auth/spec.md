## ADDED Requirements

### Requirement: Dashboard authentication resolves against user accounts

The system SHALL decide whether a dashboard install requires sign-in, who a session belongs to, and what that session may do from `dashboard_users`, never from the legacy `dashboard_settings` credential columns. The derived local auth state MUST expose: whether any account exists, the number of active accounts, the number of active accounts holding a password, whether sign-in is required (an active account holds a password or an external identity), and the id of the sole active password-holding account when exactly one exists. The state MUST be served from a per-process cache with a time-to-live of at most 5 seconds that is dropped whenever the `dashboard_users` cache-invalidation namespace is bumped, and every user write MUST bump that namespace. An install whose state does not require sign-in MUST keep today's behaviour: local requests act as the implicit admin (`auth_method` `local_bootstrap`) and remote requests require the bootstrap token or guest access.

#### Scenario: Passwordless install is unchanged

- **GIVEN** no active account holds a password or identity
- **WHEN** a local request reaches a dashboard route
- **THEN** it is served as the implicit admin with no user id
- **AND** a remote request without guest access is refused with `bootstrap_required`

#### Scenario: Legacy credential columns are not consulted

- **GIVEN** `dashboard_settings.password_hash` is set but no active account holds a password
- **WHEN** the session dependency evaluates a local request
- **THEN** the install is treated as passwordless and the implicit admin is served

### Requirement: Session cookies bind to an account and are re-validated on every request

Account session cookies SHALL carry payload version `2` with the account id (`uid`), the account's `session_generation` at issue time (`sg`), issued-at and expiry times, whether the password (`pv`) and TOTP (`tp`) steps were completed, and the authentication method (`am`). Guest cookies SHALL carry version `2`, a `guest` marker, and the guest session generation (`gg`). Cookies whose payload lacks version `2`, carries the previous keys (`pw`, `tv`, `role`, `gv`), or is malformed MUST be rejected. On every request the system MUST load the account named by `uid` and refuse the session with `401 authentication_required` when the account does not exist, is not `active`, or its `session_generation` differs from `sg`. The principal built for a valid account session MUST carry the account id, username, role slug, authentication method, and the grants resolved from the account's role; its wire `role` MUST remain `admin`.

#### Scenario: Previous-release cookie is refused

- **WHEN** a request presents a cookie whose payload uses the `pw`/`tv`/`role` keys
- **THEN** the session is treated as absent and protected routes answer `401 authentication_required`

#### Scenario: Disabled account loses its sessions

- **GIVEN** an account with an outstanding session cookie
- **WHEN** the account's `status` becomes `disabled`
- **THEN** requests carrying that cookie answer `401 authentication_required`

#### Scenario: Generation bump revokes cookies

- **GIVEN** an account with an outstanding session cookie
- **WHEN** the account's `session_generation` is incremented
- **THEN** requests carrying that cookie answer `401 authentication_required`
- **AND** a cookie issued after the bump is accepted

### Requirement: Password login resolves the account

`POST /api/dashboard-auth/password/login` SHALL accept `{username?, password}`; `username` MUST be at most 64 characters. Usernames MUST be normalized by trimming and case-folding before lookup, and only usernames matching `[a-z0-9._-]{1,64}` after normalization MAY be recorded in the audit log (others are recorded as null with reason `invalid_username`). When `username` is omitted the system MUST use the sole active password-holding account if exactly one exists and otherwise answer `422 username_required` without consuming any rate-limit budget. When no active account holds a password the system MUST answer `400 password_not_configured` without consuming budget. Any credential failure — unknown username, inactive account, wrong password — MUST answer `401 invalid_credentials` with an identical body. A successful login MUST record the account's `last_login_at`, issue a version-2 account cookie with `pv` true and `tp` false, and audit `login_success` with the username unless a TOTP step follows.

#### Scenario: Single-account install signs in without a username

- **GIVEN** exactly one active account holds a password
- **WHEN** `{password}` is submitted without a username
- **THEN** the session is issued for that account

#### Scenario: Second account makes the username mandatory

- **GIVEN** two active accounts hold passwords
- **WHEN** `{password}` is submitted without a username
- **THEN** the response is `422 username_required`
- **AND** the per-client failure counter is not incremented

#### Scenario: Unknown and wrong-password failures are indistinguishable

- **WHEN** one login names an account that does not exist and another names a real account with the wrong password
- **THEN** both answer `401 invalid_credentials` with the same body

### Requirement: Per-user TOTP with an enrolment state

TOTP secrets and replay counters SHALL be stored per account and operated on the signed-in account (`/totp/setup/start` labels the otpauth URI with the username, `/totp/setup/confirm` stores the account's secret, `/totp/verify` and `/totp/disable` check the account's secret and advance its replay counter). The global `totp_required_on_login` setting SHALL mean that every account with a password must complete TOTP. When it is enabled and the account has a secret, a session without `tp` MUST be refused with `401 totp_required`. When it is enabled and the account has no secret, the session MUST be issued in the `totp_enrollment_required` state: `GET /api/dashboard-auth/session` reports `totp_enrollment_required: true`, and only the following self-service routes serve the session: `GET /api/dashboard-auth/session`, `POST /api/dashboard-auth/totp/setup/start`, `POST /api/dashboard-auth/totp/setup/confirm`, `POST /api/dashboard-auth/totp/verify`, `POST /api/dashboard-auth/logout`, `POST /api/dashboard-auth/logout-all`, `GET /api/dashboard-auth/me`, and `POST /api/dashboard-auth/password/change`. Every other dashboard route — including the guest-password, guest logout-all, password-removal and TOTP-disable routes under the same prefix — MUST answer `403 totp_enrollment_required`. Password verification MUST run exactly one password-hash comparison whether or not the named account exists or holds a password, so response timing does not reveal which usernames are real.

#### Scenario: Account without a secret must enrol first

- **GIVEN** `totp_required_on_login` is enabled and the signed-in account has no TOTP secret
- **WHEN** the account requests `/api/settings`
- **THEN** the response is `403 totp_enrollment_required`
- **AND** `/api/dashboard-auth/session` answers `200` with `totp_enrollment_required: true`
- **AND** `/api/dashboard-auth/totp/setup/start` succeeds
- **AND** `POST /api/dashboard-auth/guest/password`, `DELETE /api/dashboard-auth/password` and `POST /api/dashboard-auth/totp/disable` answer `403 totp_enrollment_required`

#### Scenario: Replayed code is refused for the account

- **WHEN** a TOTP code already accepted for the account's current step is submitted again
- **THEN** the request answers `400 invalid_totp_code`
- **AND** the account's replay counter is unchanged

### Requirement: Roles without all-scope dashboard read are refused at the session gate

Until own-scoped dashboard views declare their own route requirements, the session dependency MUST refuse any account session whose resolved grants do not include `dashboard:read` at scope `all` with `403 permission_required` and `param: dashboard:read`. Signing in as such an account MUST still succeed and `GET /api/dashboard-auth/session` MUST still describe the session, so the account can be told why it sees nothing.

#### Scenario: Member account cannot read all-scope views

- **GIVEN** an active account holding the `member` preset with a password
- **WHEN** it signs in and requests `GET /api/accounts` and `GET /api/request-logs`
- **THEN** the login answers `200` and `GET /api/dashboard-auth/session` reports `authenticated: true`
- **AND** both reads answer `403 permission_required` with `param: dashboard:read`

### Requirement: First-run setup is compare-and-set

`POST /api/dashboard-auth/password/setup` MUST write the first credential with a compare-and-set: a missing `admin` row is inserted (a concurrent insert fails on the unique username), an existing credential-less row is re-armed only where `password_hash IS NULL`; when zero rows change the request MUST answer `409 password_already_configured` and MUST NOT touch the legacy columns or the bootstrap token. Exactly one of any set of concurrent setups succeeds and only its password signs in.

#### Scenario: Concurrent setups admit exactly one password

- **WHEN** two `POST /api/dashboard-auth/password/setup` requests race
- **THEN** one answers `200` and the other `409 password_already_configured`
- **AND** only the password of the `200` response signs in

### Requirement: Guest cookies carry the generation they were verified against

The guest login MUST read the settings row once from the database, verify the guest credential against that row, and stamp the cookie with that same row's `guest_session_generation`; it MUST NOT re-read the generation after verification.

#### Scenario: Guest password enabled between check and mint

- **GIVEN** passwordless guest access and a guest login whose credential check has just passed
- **WHEN** an admin enables a guest password (bumping the generation) before the cookie is minted
- **THEN** the minted cookie carries the pre-bump generation and is refused on the next request

### Requirement: Session revocation and self-service account routes

`POST /api/dashboard-auth/password/change` SHALL, after verifying the current password, store the new hash, increment the account's `session_generation`, and return a fresh cookie for the caller whose lifetime does not exceed the remaining lifetime of the replaced session, so every other session of the account ends while the caller stays signed in. `POST /api/dashboard-auth/logout-all` SHALL require a password-verified account session, increment the account's `session_generation`, clear the caller's cookie, and audit `user_sessions_revoked`. `DELETE /api/dashboard-auth/password` SHALL be allowed only when the caller is the single active account and holds no external identity; otherwise it MUST answer `409 other_users_exist`. When allowed it MUST clear the account's password, TOTP secret, replay counter, and identities, keep the account row, increment its generation, regenerate the bootstrap token as before, and audit `password_removed`. A later `POST /api/dashboard-auth/password/setup` on that passwordless install MUST re-arm the same account. `GET /api/dashboard-auth/me` SHALL return `{id, username, display_name, email, role: {id, slug, name, kind}, auth_method, totp_configured, must_change_password}` for a fully authenticated account session and `401 user_account_required` for guests, implicit admins, and unauthenticated callers.

#### Scenario: Password change keeps this device signed in and signs out the others

- **GIVEN** the same account is signed in from two clients
- **WHEN** one client changes the password
- **THEN** that client receives a new cookie and keeps access
- **AND** the other client's requests answer `401 authentication_required`

#### Scenario: Logout everywhere

- **GIVEN** the same account is signed in from two clients
- **WHEN** one client calls `POST /api/dashboard-auth/logout-all`
- **THEN** both clients' previous cookies are refused

#### Scenario: Password removal refused with other accounts

- **GIVEN** two active accounts exist
- **WHEN** the admin calls `DELETE /api/dashboard-auth/password`
- **THEN** the response is `409 other_users_exist`

### Requirement: Session response describes the account and the login screen

`GET /api/dashboard-auth/session` (and the session-issuing endpoints) SHALL add the optional fields `user` (`{id, username, display_name, role: {id, slug, name, kind}}` or null), `auth_method`, `must_change_password`, `totp_enrollment_required`, `login`, `access_summary`, and `assignable_role_ids`. `login` MUST be present for every caller, MUST never contain a username, and MUST set `username_field` to `hidden` exactly when one active account holds a password and to `shown` otherwise. `access_summary` (`users_total`, `users_active`, `users_invited`, `users_disabled`, `pending_invites`, `non_admin_users`, `custom_roles`, `providers_enabled`, `role_mappings`, `scim_tokens`, `audit_sinks`, `local_login_policy`) and a non-empty `assignable_role_ids` MUST be returned only to an authenticated principal holding `users:manage`; every other caller MUST receive `null` and `[]`. `assignable_role_ids` MUST list exactly the `admin`, `operator`, and `viewer` presets in this release (`member` becomes assignable when own-scoped views ship; `guest` never). `permissions` MUST keep the `read`/`write` aliases and additionally list every grant as `<permission>:<scope>`. `role` MUST remain `admin` or `guest`. `password_required` MUST equal the derived "sign-in required" state.

#### Scenario: Signed-in admin sees the account block and the summary

- **WHEN** an admin account requests the session
- **THEN** `user.username` is the account's username, `auth_method` is `password`, `login.username_field` is `hidden`
- **AND** `access_summary.users_total` counts every account and `permissions` contains `read`, `write`, and `users:manage:all`

#### Scenario: Guest and unauthenticated callers get no team facts

- **WHEN** a guest session or an unauthenticated client requests the session
- **THEN** `access_summary` is null, `assignable_role_ids` is empty, and `user` is null
- **AND** `login` is present without any username

## MODIFIED Requirements

### Requirement: Login rate limiting

The system SHALL rate-limit failed password login attempts using the existing `TotpRateLimiter` pattern: maximum 8 failures per 60-second window per client. On rate limit breach, the system MUST return 429 with a `Retry-After` header and a body that does not reveal whether the submitted username exists. Requests rejected because password login is not configured or because the username is required MUST NOT consume that failed-login budget. The system MUST NOT lock an account after failed attempts (a remotely triggerable lockout is not acceptable); per-account soft delay is deferred.

#### Scenario: Rate limit triggered

- **WHEN** 8 failed login attempts occur within 60 seconds
- **THEN** the 9th attempt returns 429 with `Retry-After` header indicating seconds until the window resets

#### Scenario: Rate limit resets on success

- **WHEN** a successful login occurs after failed attempts
- **THEN** the failure counter for that client resets to zero

#### Scenario: Unconfigured password login does not spend rate-limit budget

- **WHEN** no password is configured and a login request is submitted
- **THEN** the system returns `password_not_configured`
- **AND** it does not consume one of the failed-login attempts for that client

#### Scenario: Username required does not spend rate-limit budget

- **WHEN** two active accounts hold passwords and a login request omits the username
- **THEN** the system returns `422 username_required`
- **AND** it does not consume one of the failed-login attempts for that client

### Requirement: Dashboard password sessions use a configurable absolute lifetime

The system SHALL issue dashboard password-authenticated sessions with an absolute lifetime controlled by persisted dashboard settings. The default persisted lifetime SHALL be 1 year. Configured lifetimes at or below 30 days SHALL apply to newly issued dashboard password sessions by setting both the encrypted session expiry payload and the cookie `Max-Age` to the same value. Configured lifetimes above 30 days SHALL apply only in standard dashboard auth mode when the request is socket-level local, or when an explicit loopback-host-header override is enabled, the request uses a loopback dashboard URL, and every field value of every forwarded client-IP header is empty. Non-loopback, proxy-aware, trusted-header, or bridge-without-override requests MUST receive a 12-hour effective lifetime without rewriting the persisted setting. Sessions issued to accounts holding the `admin` preset role MUST additionally be capped at 12 hours whenever the request does not qualify for a long local session, regardless of the configured lifetime.

#### Scenario: Newly issued dashboard password session honors configured lifetime

- **WHEN** an admin configures a dashboard session lifetime and successfully completes password authentication from a socket-level local request
- **THEN** the newly issued dashboard session expires after the configured absolute lifetime
- **AND** the cookie `Max-Age` matches the same configured lifetime

#### Scenario: Long localhost-published bridge session requires explicit override

- **WHEN** an admin configures a dashboard session lifetime greater than 30 days and successfully completes password authentication through a loopback dashboard URL whose socket peer is not loopback
- **AND** the explicit loopback-host-header override is disabled
- **THEN** the newly issued dashboard session expires after 12 hours

#### Scenario: Long localhost-published bridge session can opt in

- **WHEN** an admin configures a dashboard session lifetime greater than 30 days and successfully completes password authentication through a loopback dashboard URL whose socket peer is not loopback
- **AND** the explicit loopback-host-header override is enabled
- **AND** every field value of every forwarded client-IP header is empty
- **THEN** the newly issued dashboard session expires after the configured absolute lifetime

#### Scenario: Later duplicate forwarded client identity disables the long session override

- **WHEN** a non-loopback socket peer authenticates through a loopback dashboard URL with the explicit loopback-host-header override enabled
- **AND** a forwarded client-IP header contains an empty first field followed by a non-empty field
- **THEN** the newly issued dashboard session expires after 12 hours
- **AND** the cookie `Max-Age` is `43200`

#### Scenario: Long dashboard password session falls back for non-loopback access

- **WHEN** an admin configures a dashboard session lifetime greater than 30 days and successfully completes password authentication from a non-loopback, proxy-aware, or trusted-header request
- **THEN** the newly issued dashboard session expires after 12 hours
- **AND** the cookie `Max-Age` is `43200`

#### Scenario: Existing dashboard sessions keep their original expiry

- **WHEN** an admin changes the configured dashboard session lifetime after a session cookie has already been issued
- **THEN** previously issued cookies continue to expire according to the expiry embedded in their encrypted payload
- **AND** only newly issued dashboard password sessions use the updated lifetime

#### Scenario: Remote admin session is capped even under thirty days

- **WHEN** the configured lifetime is 30 days and an account holding the `admin` preset completes password authentication from a non-loopback request
- **THEN** the newly issued session expires after 12 hours
- **AND** an account holding the `operator` preset receives the full 30 days from the same request
