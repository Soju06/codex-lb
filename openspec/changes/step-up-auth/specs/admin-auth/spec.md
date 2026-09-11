## ADDED Requirements

### Requirement: Sensitive mutations require a recent step-up

Every non-safe request (`POST`, `PUT`, `PATCH`, `DELETE`) authorised by `security:write`, `users:manage`, `roles:manage` or `accounts:export` (the `STEP_UP_PERMISSIONS` set) SHALL additionally require that the acting account re-verified a credential within the last 300 seconds. `PUT /api/settings` SHALL require it exactly when the request changes the stored value of a security field (the existing changed-field rule). Reads SHALL never require it. Principals without an account (the implicit local admin, the disabled-auth principal) have nothing to re-verify and SHALL NOT be gated; every account principal SHALL be, whatever provider signed it in, and a provider's `idp_mfa_enforced` flag SHALL NOT waive it. The step-up is recorded by the `su` claim of the session cookie, by the step-up cookie for the same account, or by a password session of the same account accompanying a trusted-header request. When none is fresh the request SHALL answer `403 step_up_required` with `param` naming the permission and `details.methods` listing the factors the account must present (`password` when it holds a password hash, `totp` when it holds a TOTP secret, both when it holds both); when the account holds neither the request SHALL answer `403 step_up_unavailable` with the message "Set up two-factor authentication or a local password to change security settings".

#### Scenario: Stale session is asked to confirm

- **GIVEN** a password account whose last step-up is older than 300 seconds
- **WHEN** it sends `PUT /api/settings` changing `guest_access_enabled`
- **THEN** the response is `403 step_up_required` with `param` `security:write` and `details.methods` `["password"]`
- **AND** `GET /api/settings` and a `PUT /api/settings` that changes only non-security fields succeed

#### Scenario: Reverse-proxy account without any factor

- **GIVEN** a trusted-header account with no password and no TOTP secret
- **WHEN** it sends `PATCH /api/auth-providers/{id}`
- **THEN** the response is `403 step_up_unavailable`
- **AND** `GET /api/auth-providers` still succeeds

#### Scenario: Passwordless local install is not asked

- **WHEN** the implicit local admin sends `POST /api/dashboard-auth/guest/password`
- **THEN** the request succeeds without a step-up

### Requirement: Step-up endpoint and cookies

`POST /api/dashboard-auth/step-up` `{password?, code?}` SHALL re-verify the signed-in account principal (session cookie or trusted-header identity; guests and account-less principals answer `401 user_account_required`). The account MUST present every factor it holds: its password when it has a hash, its TOTP code when it has a secret (the replay counter advances). Any refusal SHALL answer `401 invalid_credentials` with an identical body; attempts SHALL spend the per-client password limiter budget (8 per 60 s, `429 step_up_rate_limited`); an account with no factor SHALL answer `403 step_up_unavailable`. Success SHALL audit `step_up_verified` (actor = the account, `details.methods`) and answer `{verifiedAt, expiresAt = verifiedAt + 300}`. For a password session it SHALL re-issue the session cookie with `su = now`, keeping the method, TOTP step and remaining lifetime; for a trusted-header account it SHALL set the cookie `codex_lb_step_up` (sealed `{v: 1, uid, su, exp = su + 300}`, HttpOnly, SameSite=Lax, Secure on HTTPS, five-minute max-age), which SHALL be honoured only when its `uid` is the acting account. Password login and invite acceptance SHALL mint `su = now` when the account has no TOTP secret; `/totp/verify` SHALL mint `su = now`; a password change SHALL copy `su` unchanged into the re-issued cookie.

#### Scenario: Password account confirms and the change goes through

- **GIVEN** a password account without TOTP that was refused with `step_up_required`
- **WHEN** it posts `{password}` to `/step-up` and repeats the change
- **THEN** `/step-up` answers `200` with `verifiedAt` and `expiresAt`, the session cookie now carries `su`, the change succeeds, and a `step_up_verified` audit row names the account

#### Scenario: Password account with TOTP presents both

- **GIVEN** a password account with a TOTP secret
- **WHEN** it posts only `{password}` or only `{code}`
- **THEN** the response is `401 invalid_credentials`
- **AND** posting both succeeds and the same code is refused afterwards

#### Scenario: Reverse-proxy account confirms by code

- **GIVEN** a trusted-header account that enrolled TOTP and has no password
- **WHEN** it posts `{code}` to `/step-up`
- **THEN** the response sets `codex_lb_step_up`, the sensitive route succeeds for that account and stays refused for another identity presenting the same cookie
- **AND** 301 seconds later the route answers `403 step_up_required` again

## MODIFIED Requirements

### Requirement: Session cookies bind to an account and are re-validated on every request

Account session cookies SHALL carry payload version `2` with the account id (`uid`), the account's `session_generation` at issue time (`sg`), issued-at and expiry times, whether the password (`pv`) and TOTP (`tp`) steps were completed, the authentication method (`am`), and optionally the unix time of the account's last step-up re-verification (`su`). Guest cookies SHALL carry version `2`, a `guest` marker, and the guest session generation (`gg`). Cookies whose payload lacks version `2`, carries the previous keys (`pw`, `tv`, `role`, `gv`), or is malformed MUST be rejected. On every request the system MUST load the account named by `uid` and refuse the session with `401 authentication_required` when the account does not exist, is not `active`, or its `session_generation` differs from `sg`. The principal built for a valid account session MUST carry the account id, username, role slug, authentication method, the grants resolved from the account's role, and `su` as `step_up_verified_at`; its wire `role` MUST remain `admin`.

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

#### Scenario: Cookie without a step-up

- **WHEN** a version-2 cookie carries no `su`
- **THEN** the session is accepted and its principal's `step_up_verified_at` is absent

### Requirement: Per-user TOTP with an enrolment state

TOTP secrets and replay counters SHALL be stored per account and operated on the signed-in account (`/totp/setup/start` labels the otpauth URI with the username, `/totp/setup/confirm` stores the account's secret, `/totp/verify` and `/totp/disable` check the account's secret and advance its replay counter). These routes SHALL accept a password session or, in `trusted_header` mode, the account the identity header resolves to, so an account without a password can enrol its only step-up method: for such an account `/totp/verify` SHALL set the step-up cookie instead of a session cookie, `/totp/disable` SHALL require only a valid code, and `/totp/disable` SHALL answer `{status, stepUpAvailable}` where `stepUpAvailable` is false when the account holds no password. The global `totp_required_on_login` setting SHALL mean that every account with a password must complete TOTP. When it is enabled and the account has a secret, a session without `tp` MUST be refused with `401 totp_required`. When it is enabled and the account has no secret, the session MUST be issued in the `totp_enrollment_required` state: `GET /api/dashboard-auth/session` reports `totp_enrollment_required: true`, and only the following self-service routes serve the session: `GET /api/dashboard-auth/session`, `POST /api/dashboard-auth/totp/setup/start`, `POST /api/dashboard-auth/totp/setup/confirm`, `POST /api/dashboard-auth/totp/verify`, `POST /api/dashboard-auth/logout`, `POST /api/dashboard-auth/logout-all`, `GET /api/dashboard-auth/me`, and `POST /api/dashboard-auth/password/change`. Every other dashboard route — including the guest-password, guest logout-all, password-removal and TOTP-disable routes under the same prefix — MUST answer `403 totp_enrollment_required`. Password verification MUST run exactly one password-hash comparison whether or not the named account exists or holds a password, so response timing does not reveal which usernames are real.

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

#### Scenario: Reverse-proxy account enrols without a password

- **GIVEN** a trusted-header account with no password
- **WHEN** it calls `/totp/setup/start` and `/totp/setup/confirm` with a valid code
- **THEN** both succeed and the account's secret is stored
- **AND** `/totp/disable` with a valid code answers `{status: "ok", stepUpAvailable: false}`

### Requirement: Session response describes the account and the login screen

`GET /api/dashboard-auth/session` (and the session-issuing endpoints) SHALL add the optional fields `user` (`{id, username, display_name, role: {id, slug, name, kind}}` or null), `auth_method`, `must_change_password`, `totp_enrollment_required`, `login`, `access_summary`, `assignable_role_ids`, and `step_up`. `login` MUST be present for every caller, MUST never contain a username, MUST set `username_field` to `hidden` exactly when one active account holds a password and to `shown` otherwise, MUST list the active providers in `providers` (`{kind, provider_key, label, login_url}`; `password` first, `trusted_header` when active, `login_url` null), and MUST set `pending_identity` to true exactly when the request carried a provider identity that resolved to no active account. `access_summary` (`users_total`, `users_active`, `users_invited`, `users_disabled`, `pending_invites`, `non_admin_users`, `custom_roles`, `providers_enabled` = the active provider kinds, `role_mappings`, `scim_tokens`, `audit_sinks`, `local_login_policy`) and a non-empty `assignable_role_ids` MUST be returned only to an authenticated principal holding `users:manage`; every other caller MUST receive `null` and `[]`. `assignable_role_ids` MUST list exactly the `admin`, `operator`, and `viewer` presets in this release (`member` becomes assignable when own-scoped views ship; `guest` never). `step_up` (`{verified_at, expires_at, methods}`) MUST be returned for signed-in accounts only: `verified_at`/`expires_at` are set when a step-up recorded for the account is still within 300 seconds (from the session cookie's `su` or the step-up cookie) and null otherwise, and `methods` lists the factors the account must present (`password`, `totp`, both, or none); every other caller MUST receive `null`. `permissions` MUST keep the `read`/`write` aliases and additionally list every grant as `<permission>:<scope>`. `role` MUST remain `admin` or `guest`. `password_required` MUST equal the derived "sign-in required" state, except that a header-less request in `trusted_header` mode while no active account holds a password reports `password_required=false` and `authenticated=false` (the client shows the reverse-proxy notice, not a login form). A trusted-header request resolved to an account SHALL be described like that account's session (`authenticated=true`, `user`, `auth_method=trusted_header`, the account's permissions, team facts when it holds `users:manage`); `password_session_active` reports whether a fallback password cookie also rode along.

#### Scenario: Signed-in admin sees the account block and the summary

- **WHEN** an admin account requests the session
- **THEN** `user.username` is the account's username, `auth_method` is `password`, `login.username_field` is `hidden`
- **AND** `access_summary.users_total` counts every account and `permissions` contains `read`, `write`, and `users:manage:all`

#### Scenario: Guest and unauthenticated callers get no team facts

- **WHEN** a guest session or an unauthenticated client requests the session
- **THEN** `access_summary` is null, `assignable_role_ids` is empty, `user` is null and `step_up` is null
- **AND** `login` is present without any username

#### Scenario: Refused proxy identity

- **WHEN** a trusted-header request whose identity resolves to no active account asks for the session
- **THEN** `authenticated` is false, `user` is null and `login.pending_identity` is true

#### Scenario: Step-up block follows the account

- **GIVEN** a password account without TOTP that signed in 301 seconds ago and has not stepped up since
- **WHEN** it requests the session
- **THEN** `step_up` is `{verified_at: null, expires_at: null, methods: ["password"]}`
- **AND** after a successful `/step-up` the block carries `verified_at` and `expires_at = verified_at + 300`
