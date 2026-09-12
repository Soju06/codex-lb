## ADDED Requirements

### Requirement: The local login policy is a database-only setting

`dashboard_settings.local_login_policy` SHALL hold `enabled` (the default), `admins_only` or `break_glass_only`, and SHALL be created by one Alembic revision parented to the current single head with the same idempotent inspector guard and mirrored downgrade as the other `dashboard_settings` columns. It SHALL have no environment variable, no `Settings` field and no entry in the configuration-tier registry: it is a policy an operator changes at runtime through `PUT /api/settings`, and an environment override would let a redeploy silently re-open local sign-in that a company had closed. It SHALL be a security setting, so writing it requires `security:write` and a step-up recorded within the last five minutes, and every change SHALL audit `login_policy_changed` with the previous and the new value. A write that repeats the stored value is not a change and SHALL NOT trip the gates below.

#### Scenario: Existing installs are unaffected by the migration

- **GIVEN** an install upgrading from the previous release
- **WHEN** the migration runs
- **THEN** `local_login_policy` is `enabled` and every account that could sign in before still can

#### Scenario: The policy has no environment variable

- **WHEN** the configuration-tier check and the `.env.example` budget run
- **THEN** `local_login_policy` appears in neither, because it is not a `Settings` field

#### Scenario: Writing the policy is a security change

- **GIVEN** a signed-in admin whose last step-up was more than five minutes ago
- **WHEN** it submits `local_login_policy: "admins_only"`
- **THEN** the response is `403 step_up_required` and the stored policy is unchanged

### Requirement: A qualifying break-glass account

`dashboard_users.is_break_glass` SHALL be a **designation** and nothing more: it MAY be set on an account holding the admin preset that has no second factor — the migration that created the compat `admin` account backfilled exactly that — and it SHALL NOT be settable on any other role. An account SHALL be **qualifying** only when all five facts hold: `is_break_glass` is set, `status` is `active`, its role is the admin preset, `totp_secret_encrypted` is not null, and `password_hash` is not null. The local password is one of the facts because the whole purpose of a qualifying account is to open the *local password form* while the identity provider is down: an account provisioned by a sign-in provider that never set a password cannot use that form, so counting it would let an install reach a state where the policy is tightened and nothing can sign in locally. Qualification SHALL be computed from the current row on every read, never stored, so enrolling or removing a second factor changes it without a separate write. Every gate in this capability counts qualifying accounts; nothing counts designations.

#### Scenario: The migrated admin is designated but not qualifying

- **GIVEN** an install upgraded from the legacy credential columns whose `admin` account has no TOTP secret
- **WHEN** the qualifying accounts are counted
- **THEN** the count is zero, while the `admin` account still reports `is_break_glass`

#### Scenario: A designated admin with no local password does not qualify

- **GIVEN** an active designated admin holding a TOTP secret but no `password_hash`
- **WHEN** the qualifying accounts are counted
- **THEN** the count is zero
- **AND** tightening `local_login_policy` answers `409 break_glass_requires_totp`

#### Scenario: Enrolling a second factor qualifies the account

- **WHEN** that account completes `/totp/setup/confirm`
- **THEN** the qualifying count becomes one without any further write to `is_break_glass`

#### Scenario: The designation is limited to the admin preset

- **WHEN** an admin sets `is_break_glass` on an operator account
- **THEN** the response is `422` and the flag is unchanged

### Requirement: Break-glass sign-in is audited at critical severity

A sign-in of an account carrying the break-glass designation **that completes a second factor** SHALL emit one audit row `break_glass_login` with `severity` `critical`, the account as actor and target, the authentication method, and the client address, in addition to the ordinary `login_success` row. The session it mints SHALL be marked as an emergency session so the dashboard can say so. A designated account holding no secret presents no factor, so its sign-in SHALL NOT write the row — otherwise every upgraded install would record a critical event on each ordinary sign-in of its `admin` account, and the severity would stop meaning anything. That account SHALL still be able to sign in and reach the enrolment routes while the policy is `enabled`, so the migrated install can never lock itself out of qualifying; the state it signs in under is specified in **A designation without a second factor signs in as an ordinary admin** below.

#### Scenario: The emergency sign-in is recorded

- **WHEN** a break-glass admin completes password and TOTP
- **THEN** an audit row `break_glass_login` exists with severity `critical` naming that account
- **AND** an ordinary admin signing in writes no such row

#### Scenario: The migrated admin can still get in and enrol

- **GIVEN** `local_login_policy` is `enabled` and the designated `admin` account has no TOTP secret
- **WHEN** it signs in with its password
- **THEN** the session is issued and the enrolment routes serve it

### Requirement: A designation without a second factor signs in as an ordinary admin

The always-two-factor rule binds an account that **holds** a secret, so a designated account with `totp_secret_encrypted` null SHALL meet the install's own TOTP policy and nothing more — it SHALL NOT be asked for a factor it does not hold, and it SHALL NOT be refused for lacking one. Under `enabled` and `admins_only` its password step SHALL therefore succeed exactly as it would for any other admin of that install: with both `totp_required_on_login` and `totp_required_for_admin_role` off the session is fully authenticated, and when either applies the session SHALL be issued in the `totp_enrollment_required` state already specified for accounts without a secret (`authenticated: true`, `totp_enrollment_required: true`, `/totp/setup/start` and `/totp/setup/confirm` served, every other dashboard route `403 totp_enrollment_required`). That is the path an upgraded install walks to reach its first qualifying account, so neither state may be closed off. Under `break_glass_only` the same account SHALL be refused `401 invalid_credentials` like any other account the policy does not admit, because the strictest policy admits qualification and not designation. The session SHALL still report `break_glass_session: true` — the indicator names the designation, which is the rule the session response states — while the critical `break_glass_login` row belongs to the second factor and SHALL NOT be written. Once the account confirms a secret the enrolled rule takes over from the next request: the enrolment itself mints no TOTP-verified session, so the session it enrolled from SHALL be refused `401 totp_required` until `/totp/verify` completes it.

#### Scenario: An install that requires two-factor sends the designation to enrolment

- **GIVEN** `totp_required_on_login` is on and the designated `admin` account holds a password and no TOTP secret
- **WHEN** it submits its correct password
- **THEN** the response is `200` with `authenticated: true`, `totp_enrollment_required: true` and `break_glass_session: true`
- **AND** `/api/settings` answers `403 totp_enrollment_required` while `/totp/setup/start` and `/totp/setup/confirm` serve it
- **AND** no `break_glass_login` row exists, because no second factor was presented

#### Scenario: Enrolment hands the account to the enrolled rule

- **GIVEN** that account has just confirmed its first TOTP secret on the session it enrolled from
- **WHEN** it requests `/api/settings`
- **THEN** the response is `401 totp_required`
- **AND** `/totp/verify` with a valid code serves the next request and writes one critical `break_glass_login` row

### Requirement: Tightening the local login policy requires a qualifying break-glass account

Changing `local_login_policy` from `enabled` to `admins_only` or `break_glass_only`, and changing it between the two tightened values, SHALL require at least one qualifying break-glass account and SHALL otherwise answer `409 break_glass_requires_totp` whose body names the designated account that would qualify, so the refusal doubles as the instruction ("turn on two-factor for `<username>`"). Relaxing the policy back to `enabled` SHALL never be gated, and re-saving the value already stored SHALL never be gated. The same rule applies to enabling a sign-in provider that offers no local password fallback, which is specified in `identity-providers`.

The count and the settings write SHALL be one atomic step, not a check followed by a write: the request SHALL hold the same account write intent the account mutations take (SQLite write-intent transaction, `FOR UPDATE` over the active admin rows on PostgreSQL) across both, acquired **before its first read** so both dialects take the lock at the same moment, and released only by the commit that stores the policy. Otherwise a concurrent TOTP reset, password removal or deactivation can remove the last qualifying account between the count and the commit, leaving a restricted policy with no recovery account. A request that cannot be a tightening SHALL NOT take the lock, so ordinary settings saves are not serialised against account mutations.

#### Scenario: A concurrent account mutation cannot slip between the count and the write

- **GIVEN** exactly one qualifying break-glass account
- **WHEN** a policy tightening and a mutation that would un-qualify that account are processed concurrently
- **THEN** either the tightening is refused with `409 break_glass_requires_totp`, or the mutation is refused with `409 last_break_glass_protected`
- **AND** the stored policy is never restricted while the qualifying count is zero

#### Scenario: Tightening without a qualifying account is refused

- **GIVEN** the designated `admin` account has no TOTP secret
- **WHEN** an admin sets `local_login_policy` to `break_glass_only`
- **THEN** the response is `409 break_glass_requires_totp`, its body names `admin`, and the stored policy is still `enabled`

#### Scenario: Tightening succeeds once the account qualifies

- **WHEN** the same admin enrols a second factor and repeats the change
- **THEN** the response is `200`, the policy is `break_glass_only`, and `login_policy_changed` is audited

#### Scenario: Relaxing is always allowed

- **GIVEN** `local_login_policy` is `break_glass_only` and no account qualifies any more
- **WHEN** an admin sets it back to `enabled`
- **THEN** the response is `200`

#### Scenario: Re-saving the settings form is not a tightening

- **GIVEN** `local_login_policy` is already `admins_only` and no account qualifies
- **WHEN** the settings form is submitted unchanged
- **THEN** the response is `200` and no break-glass gate runs

### Requirement: Host recovery commands

The `codex-lb` console entry point SHALL carry an `admin` command group with exactly four recovery commands that act on the configured database directly, take no environment variable of their own, make no network call, and never import the web application: `admin reset-password <username>` (prompts for the new password, never accepting it on the command line, revokes the account's sessions, and with `--clear-two-factor` also removes its second-factor secret, because a designated account that holds one must always present it and a lost authenticator would otherwise be a permanent lockout), `admin local-login enable` (sets `local_login_policy` back to `enabled`), `admin disable-provider <id>` (clears a provider row's `enabled`), and `admin reset-login-policy` (sets the policy back to `enabled` and disables every non-password sign-in provider, so an identity the provider keeps refusing cannot go on pre-empting the local session, while the password provider — the recovery path itself — is kept). Each SHALL write one audit row `break_glass_cli_used` with `severity` `critical` and `auth_method` `cli`, naming the command and its target and carrying no acting account, in the same transaction as the change it makes. `codex-lb admin` without a sub-command SHALL exit non-zero with a message and MUST NOT fall through into the server launch path. A database with no schema SHALL produce a readable instruction rather than a traceback. These commands SHALL bypass the policy and provider gates by design: they are the recovery path for the lockout those gates are meant to prevent.

`admin reset-password` SHALL NOT be able to leave the install unreachable through the very form it just handed out a password for. After its write, and in the same transaction, it SHALL determine whether any account would still be admitted by the stored `local_login_policy` — the same admission rule the login path applies, over active accounts that hold a password — and when none would, it SHALL set the policy back to `enabled`, report the old and new values, and record the re-open in its audit row. A policy that still admits at least one such account SHALL be left exactly as it is, and `enabled` is never touched: this is a lockout repair, not a way to relax a policy. The case this exists for is `--clear-two-factor` on the last designated admin under `break_glass_only`, where clearing the secret is precisely what stops the account qualifying.

#### Scenario: Re-opening local sign-in from the host

- **GIVEN** `local_login_policy` is `break_glass_only` and the identity provider is unreachable
- **WHEN** the operator runs `codex-lb admin local-login enable` on the host
- **THEN** the stored policy becomes `enabled` without any qualifying-account check
- **AND** one `break_glass_cli_used` row with severity `critical` and `auth_method` `cli` is written

#### Scenario: Resetting a password without exposing it

- **WHEN** the operator runs `codex-lb admin reset-password admin`
- **THEN** the command prompts for the password instead of reading it from the arguments
- **AND** the stored hash changes, the account's sessions are invalidated, and `break_glass_cli_used` is audited

#### Scenario: A lost authenticator does not strand the emergency account

- **GIVEN** the only qualifying break-glass account enrolled a second factor and the authenticator is gone
- **WHEN** the operator runs `codex-lb admin reset-password <username> --clear-two-factor`
- **THEN** the account's secret is removed, the install-wide two-factor requirement is left as configured, and the account signs in with the new password and enrols again

#### Scenario: Clearing the last second factor re-opens local sign-in

- **GIVEN** `local_login_policy` is `break_glass_only` and one designated admin is the only qualifying account
- **WHEN** the operator runs `codex-lb admin reset-password <that account> --clear-two-factor`
- **THEN** the stored policy becomes `enabled`, the report names the value it came from, and the audit row records the re-open

#### Scenario: A policy that still has a way in is left alone

- **GIVEN** `local_login_policy` is `break_glass_only` and a second qualifying break-glass admin exists
- **WHEN** the operator runs `codex-lb admin reset-password <the first one> --clear-two-factor`
- **THEN** the stored policy is still `break_glass_only` and the report says nothing about re-opening it

#### Scenario: A bare admin invocation does not start a server

- **WHEN** `codex-lb admin` is run with no sub-command
- **THEN** the process exits non-zero with a message and no server is started

#### Scenario: A database without a schema explains itself

- **GIVEN** a database that has never been migrated
- **WHEN** any `codex-lb admin` command runs
- **THEN** the output is a readable instruction to run the migrations, not a traceback

## MODIFIED Requirements

### Requirement: Password login resolves the account

`POST /api/dashboard-auth/password/login` SHALL accept `{username?, password}`; `username` MUST be at most 64 characters. Usernames MUST be normalized by trimming and case-folding before lookup, and only usernames matching `[a-z0-9._-]{1,64}` after normalization MAY be recorded in the audit log (others are recorded as null with reason `invalid_username`). When `username` is omitted the system MUST use the sole active password-holding account if exactly one exists and otherwise answer `422 username_required` without consuming any rate-limit budget. When no active account holds a password the system MUST answer `400 password_not_configured` without consuming budget. Any credential failure — unknown username, inactive account, wrong password — MUST answer `401 invalid_credentials` with an identical body. A successful login MUST record the account's `last_login_at`, issue a version-2 account cookie with `pv` true and `tp` false, and audit `login_success` with the username unless a TOTP step follows. Local password sign-in SHALL additionally obey `local_login_policy`: under `enabled` (the default) every active password-holding account may sign in, under `admins_only` only an account holding the admin preset may, and under `break_glass_only` only a **qualifying** break-glass account may — the designation alone SHALL NOT admit, because every upgraded install carries the designation on an `admin` row with no second factor and admitting it would leave the strictest policy with a password-only admin door, weaker than `admins_only`. That cannot lock an install out: the policy cannot be tightened while the qualifying count is zero, and while it is tightened no mutation may take the last qualifying account away, so `break_glass_only` implies at least one admissible account; a designated account that has not enrolled yet is still admitted under `enabled` and `admins_only`, where it enrols. A refusal by policy MUST answer `401 invalid_credentials` with the same body, the same status and the same single password-hash comparison as every other credential failure, and MUST audit `login_failed` with `reason: login_policy` and the username. An account carrying the break-glass designation **that holds a TOTP secret** MUST always complete that second factor, whatever `totp_required_on_login` and `totp_required_for_admin_role` say: its password step MUST NOT issue a fully verified session on its own but leave the session pending (`200` with `authenticated: false` and `totp_required_on_login: true`), and every other dashboard route MUST answer `401 totp_required` until `POST /api/dashboard-auth/totp/verify` completes it. The designation on its own MUST NOT require a factor the account does not hold; that account is specified by **A designation without a second factor signs in as an ordinary admin**.

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

#### Scenario: Policy refuses an ordinary account

- **GIVEN** `local_login_policy` is `break_glass_only` and an active operator holds a password
- **WHEN** the operator submits its correct password
- **THEN** the response is `401 invalid_credentials`, byte-identical to the answer for an unknown username
- **AND** an audit row `login_failed` records `reason: login_policy` for that username

#### Scenario: Policy admits the account it is meant to admit

- **GIVEN** the same install and a break-glass admin holding a password and a TOTP secret
- **WHEN** it submits its correct password
- **THEN** the session is issued pending (`authenticated: false`, `totp_required_on_login: true`) and a protected request answers `401 totp_required`
- **AND** `/totp/verify` with a valid code completes the sign-in

#### Scenario: The strictest policy refuses a designation that has not enrolled

- **GIVEN** `local_login_policy` is `break_glass_only` and the designated `admin` account has no TOTP secret
- **WHEN** it submits its correct password
- **THEN** the response is `401 invalid_credentials`, byte-identical to the answer for an unknown username
- **AND** the same account signs in normally once the policy is `enabled` or `admins_only`

#### Scenario: Break-glass sign-in needs a second factor even with every TOTP toggle off

- **GIVEN** `totp_required_on_login` and `totp_required_for_admin_role` are both off, `local_login_policy` is `enabled`, and a break-glass admin holds a TOTP secret
- **WHEN** it submits its correct password
- **THEN** no verified session is issued: the session is pending and a protected request answers `401 totp_required`
- **AND** a non-break-glass admin on the same install signs in with the password alone

### Requirement: Login rate limiting

The system SHALL rate-limit failed password login attempts with **two** buckets, both spent by every attempt:

- a per-**(client address, normalized username)** bucket of 8 failures per 60-second window, so a limit reached for one username at one address bars neither that username at another address nor another username at the same address; and
- a coarse per-**client address** bucket of 60 failures per 60-second window, which is the endpoint's actual ceiling.

A successful sign-in SHALL clear the per-(address, username) bucket and SHALL NOT clear the per-address bucket. Clearing the coarse bucket on success would hand its ceiling to anyone holding one valid account on the install: seven guesses at another username, one sign-in of their own, seven more, without limit — so the only ceiling the endpoint has would be resettable on demand by the very caller it bounds. The coarse bucket SHALL instead expire with its own window, which a person signing in never meets (one sign-in against 60 per minute).

The second bucket SHALL NOT be omitted or replaced by the first. The per-account bucket is local by construction, so any address can mint a fresh bucket for every username it invents, and each attempt costs a blocking password-hash comparison on the event loop that also serves the proxy; without an address ceiling the endpoint has none. The ceiling SHALL be well above the per-account allowance (60/60 s is seven and a half times it) so that a shared NAT egress reaches the per-account limit long before the address limit, and the two SHALL use distinct limiter types so the per-account 8/60 s semantics are unchanged.

On rate limit breach either bucket answers `429` with a `Retry-After` header and a body that does not reveal whether the submitted username exists. Requests rejected because password login is not configured or because the username is required MUST NOT consume either failed-login budget; the `422 username_required` audit guard SHALL read the per-address bucket, which is the one ordinary logins increment. The system MUST NOT lock an account after failed attempts (a remotely triggerable lockout is not acceptable). **No account SHALL be exempt from either bucket**, including a break-glass account: both keys carry the client address, so an attacker exhausting a username's budget from their own address cannot bar the operator signing in from a different one, which is what "no remotely triggerable lockout" requires. An exemption would also be an enumeration oracle — whether the ninth failure for a username answers `429` or `401` would name the emergency account to an unauthenticated caller.

Because a recorded attempt is only ever cleared by a *successful* sign-in of that same username, and the key space includes a caller-supplied username, the system SHALL age recorded attempts out of the database on the leader pass of the existing periodic cleanup scheduler, independently of any sticky-session cleanup toggle.

#### Scenario: Rate limit triggered

- **WHEN** 8 failed login attempts occur within 60 seconds
- **THEN** the 9th attempt returns 429 with `Retry-After` header indicating seconds until the window resets

#### Scenario: Rate limit resets on success

- **WHEN** a successful login occurs after failed attempts
- **THEN** the per-(address, username) failure counter for that client and username resets to zero

#### Scenario: A valid account cannot reset the address ceiling

- **GIVEN** a client address that holds one valid account and has spent part of the per-address ceiling on other usernames
- **WHEN** it signs in successfully to its own account
- **THEN** the per-address counter still holds those attempts, and the attempt that exceeds the ceiling answers `429`

#### Scenario: One address cannot spray unlimited usernames

- **WHEN** one client address submits failed logins naming a different, never-seen username each time
- **THEN** the attempt that exceeds the per-address ceiling answers `429` with a `Retry-After` header
- **AND** the same username submitted from a different address is still served

#### Scenario: Recorded attempts do not accumulate forever

- **GIVEN** failed-login rows older than the retention window
- **WHEN** the periodic cleanup scheduler runs its leader pass
- **THEN** those rows are deleted, whether or not sticky-session cleanup is enabled

#### Scenario: Unconfigured password login does not spend rate-limit budget

- **WHEN** no password is configured and a login request is submitted
- **THEN** the system returns `password_not_configured`
- **AND** it does not consume one of the failed-login attempts for that client

#### Scenario: Username required does not spend rate-limit budget

- **WHEN** two active accounts hold passwords and a login request omits the username
- **THEN** the system returns `422 username_required`
- **AND** it does not consume one of the failed-login attempts for that client

#### Scenario: One address does not bar another

- **GIVEN** 8 failed attempts for `alice` from address X
- **WHEN** `alice` submits a correct password from address Y
- **THEN** the sign-in succeeds
- **AND** a different username from address X is also still served

#### Scenario: A break-glass account cannot be locked out remotely

- **GIVEN** a qualifying break-glass account
- **WHEN** an attacker exhausts that username's budget from its own address
- **THEN** the operator submitting the correct password from a different address still signs in

#### Scenario: The limiter is not an oracle

- **WHEN** 9 wrong passwords are submitted from one address for the qualifying break-glass username and for a username that does not exist
- **THEN** the ninth answer is `429` in both cases, with the same body

### Requirement: Session cookies bind to an account and are re-validated on every request

Account session cookies SHALL carry payload version `2` with the account id (`uid`), the account's `session_generation` at issue time (`sg`), issued-at and expiry times, whether the password (`pv`) and TOTP (`tp`) steps were completed, the authentication method (`am`), and optionally the unix time of the account's last step-up re-verification (`su`). Guest cookies SHALL carry version `2`, a `guest` marker, and the guest session generation (`gg`). Cookies whose payload lacks version `2`, carries the previous keys (`pw`, `tv`, `role`, `gv`), or is malformed MUST be rejected. On every request the system MUST load the account named by `uid` and refuse the session with `401 authentication_required` when the account does not exist, is not `active`, or its `session_generation` differs from `sg`. The principal built for a valid account session MUST carry the account id, username, role slug, authentication method, the grants resolved from the account's role, and `su` as `step_up_verified_at`; its wire `role` MUST remain `admin`. A session minted for an account carrying the break-glass designation SHALL additionally set the emergency marker (`bg`) on the same version-2 payload; the marker is optional and its absence means false, so cookies minted before this release keep decoding and the payload version does not change. The principal built from such a cookie MUST carry the marker.

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

#### Scenario: Emergency session is marked

- **GIVEN** a break-glass admin completes its password and TOTP steps
- **WHEN** the session cookie is decoded
- **THEN** it carries the emergency marker and the session response reports the emergency session
- **AND** a session minted for any other account carries no marker

#### Scenario: Cookie minted before this release still decodes

- **WHEN** a version-2 cookie without the emergency marker is presented
- **THEN** the session is accepted and reports no emergency session

### Requirement: Per-user TOTP with an enrolment state

TOTP secrets and replay counters SHALL be stored per account and operated on the signed-in account (`/totp/setup/start` labels the otpauth URI with the username, `/totp/setup/confirm` stores the account's secret, `/totp/verify` and `/totp/disable` check the account's secret and advance its replay counter). These routes SHALL accept a password session or, in `trusted_header` mode, the account the identity header resolves to, so an account without a password can enrol its only step-up method: for such an account `/totp/verify` SHALL set the step-up cookie instead of a session cookie, `/totp/disable` SHALL require only a valid code, and `/totp/disable` SHALL answer `{status, stepUpAvailable}` where `stepUpAvailable` is false when the account holds no password. The global `totp_required_on_login` setting SHALL mean that every account with a password must complete TOTP. When it is enabled and the account has a secret, a session without `tp` MUST be refused with `401 totp_required`. When it is enabled and the account has no secret, the session MUST be issued in the `totp_enrollment_required` state: `GET /api/dashboard-auth/session` reports `totp_enrollment_required: true`, and only the following self-service routes serve the session: `GET /api/dashboard-auth/session`, `POST /api/dashboard-auth/totp/setup/start`, `POST /api/dashboard-auth/totp/setup/confirm`, `POST /api/dashboard-auth/totp/verify`, `POST /api/dashboard-auth/logout`, `POST /api/dashboard-auth/logout-all`, `GET /api/dashboard-auth/me`, and `POST /api/dashboard-auth/password/change`. Every other dashboard route — including the guest-password, guest logout-all, password-removal and TOTP-disable routes under the same prefix — MUST answer `403 totp_enrollment_required`. Password verification MUST run exactly one password-hash comparison whether or not the named account exists or holds a password, so response timing does not reveal which usernames are real. While `local_login_policy` is not `enabled`, `/totp/disable` MUST first run the shared break-glass guard and MUST answer `409 last_break_glass_protected` when removing the secret would leave the install with no qualifying break-glass account; the secret is unchanged and the refusal names no other account.

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

#### Scenario: The last break-glass account cannot remove its own second factor

- **GIVEN** `local_login_policy` is `admins_only` and the signed-in account is the only qualifying break-glass account
- **WHEN** it posts a valid code to `/totp/disable`
- **THEN** the response is `409 last_break_glass_protected` and the account keeps its secret
- **AND** the same request succeeds once a second qualifying break-glass account exists

### Requirement: Session response describes the account and the login screen

`GET /api/dashboard-auth/session` (and the session-issuing endpoints) SHALL add the optional fields `user` (`{id, username, display_name, role: {id, slug, name, kind}}` or null), `auth_method`, `must_change_password`, `totp_enrollment_required`, `login`, `access_summary`, `assignable_role_ids`, and `step_up`. `login` MUST be present for every caller, MUST never contain a username, MUST set `username_field` to `hidden` exactly when one active account holds a password and to `shown` otherwise, MUST list the active providers in `providers` (`{kind, provider_key, label, login_url}`; `password` first, `trusted_header` when active, `login_url` null), MUST set `pending_identity` to true exactly when the request carried a provider identity that resolved to no active account, and MUST report `local_login` as the effective `local_login_policy` (`enabled`, `admins_only` or `break_glass_only`) so the login screen knows whether the local password form is shown, collapsed behind a link, or reachable only at `/login?local=1`. `login` MUST NOT name the break-glass account or any other username to an unauthenticated caller; the account name is served only to the authenticated `security:write` caller who can act on it. `access_summary` (`users_total`, `users_active`, `users_invited`, `users_disabled`, `pending_invites`, `non_admin_users`, `custom_roles`, `providers_enabled` = the active provider kinds, `role_mappings`, `scim_tokens`, `audit_sinks`, `local_login_policy`) and a non-empty `assignable_role_ids` MUST be returned only to an authenticated principal holding `users:manage`; every other caller MUST receive `null` and `[]`. `assignable_role_ids` MUST list exactly the `admin`, `operator`, and `viewer` presets in this release (`member` becomes assignable when own-scoped views ship; `guest` never). `step_up` (`{verified_at, expires_at, methods}`) MUST be returned for signed-in accounts only: `verified_at`/`expires_at` are set when a step-up recorded for the account is still within 300 seconds (from the session cookie's `su` or the step-up cookie) and null otherwise, and `methods` lists the factors the account must present (`password`, `totp`, both, or none); every other caller MUST receive `null`. `break_glass_session` MUST be true exactly when the session was minted for an account carrying the break-glass designation and false otherwise (never null for a signed-in account), so the header can show the emergency-session indicator. `permissions` MUST keep the `read`/`write` aliases and additionally list every grant as `<permission>:<scope>`. `role` MUST remain `admin` or `guest`. `password_required` MUST equal the derived "sign-in required" state, except that a header-less request in `trusted_header` mode while no active account holds a password reports `password_required=false` and `authenticated=false` (the client shows the reverse-proxy notice, not a login form). A trusted-header request resolved to an account SHALL be described like that account's session (`authenticated=true`, `user`, `auth_method=trusted_header`, the account's permissions, team facts when it holds `users:manage`); `password_session_active` reports whether a fallback password cookie also rode along.

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

#### Scenario: Unauthenticated caller learns the policy but no name

- **GIVEN** `local_login_policy` is `break_glass_only`
- **WHEN** an unauthenticated client requests the session
- **THEN** `login.local_login` is `break_glass_only` and `login.providers` lists the active providers
- **AND** the response contains no username and `access_summary` is null

#### Scenario: Emergency session is reported

- **WHEN** a break-glass admin that completed both factors requests the session
- **THEN** `break_glass_session` is true
- **AND** the same field is false for every other signed-in account

### Requirement: Trusted-header requests resolve to accounts

When `dashboard_auth_mode` is `trusted_header` and the request carries the singular trusted identity header, the session dependency SHALL resolve the identity through the identity resolver and serve the request as a **user principal** of the resolved account (its role's grants, `auth_method=trusted_header`, `auth_mode=trusted_header`, audit actor = the account) instead of an implicit admin. A role without `dashboard:read` at scope `all` is refused with `403 permission_required`. When the identity is refused (no account, or a disabled account) and the request also carries a password-verified cookie of an active account, that account SHALL be served only when `local_login_policy` admits it — `enabled` admits every active account, which is today's behaviour and therefore a no-op for the operator and viewer sessions that already work; `admins_only` admits an account holding the admin preset; `break_glass_only` admits a **qualifying** break-glass account and not a bare designation, exactly as the local password form does, so the strictest policy is never the weaker of the two here either — the emergency admin stays reachable while the proxy asserts a stranger, and a cookie the policy does not admit is treated as absent. The ad-hoc fallback branch is replaced by this one policy decision, which the session gate and the session response MUST read from the same function so `password_session_active` never advertises a fallback the gate would refuse. A resolved header account always wins over a cookie. Otherwise a refused identity SHALL answer `401 identity_not_provisioned` and a disabled account `401 account_disabled`. A header whose provider row is disabled, or whose trimmed value is empty or longer than 512 characters, SHALL be treated as absent (`401 proxy_auth_required`). Requests without the header keep today's behaviour (`401 proxy_auth_required` unless a password session is present). The TOTP policy is not applied to header sessions in this release.

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

#### Scenario: The default policy leaves the fallback untouched

- **GIVEN** `local_login_policy` is `enabled` and an operator holds a password-verified cookie
- **WHEN** the proxy asserts a stranger on a protected route
- **THEN** the request is served as that operator, exactly as it is today

#### Scenario: A tightened policy narrows the fallback

- **GIVEN** the same request while `local_login_policy` is `break_glass_only`
- **THEN** the response is `401 identity_not_provisioned` and the session response reports `password_session_active` false
- **AND** the same request with a **qualifying** break-glass admin's cookie is served as that admin
