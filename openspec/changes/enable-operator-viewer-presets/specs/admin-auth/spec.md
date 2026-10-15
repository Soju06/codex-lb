## ADDED Requirements

### Requirement: Admin-level accounts

An account SHALL count as *admin-level* when the resolved grant table of its role holds at least one privileged permission: `security:write`, `users:manage`, `roles:manage`, `accounts:export`, `conversations:read` or `audit:read`. The admin preset is therefore admin-level by definition; a custom role becomes admin-level as soon as it holds one of these permissions; the operator, member, viewer and guest presets never are. The definition MUST be derived from the grant table (`is_admin_level(grants)`), not stored, so that a role's classification follows its grants without a second source of truth.

#### Scenario: Presets

- **WHEN** the admin, operator and viewer grant tables are classified
- **THEN** admin is admin-level and operator and viewer are not

#### Scenario: Custom role with one privileged permission

- **GIVEN** a custom role granting `dashboard:read`, `accounts:read` and `audit:read`
- **WHEN** it is classified
- **THEN** it is admin-level

### Requirement: TOTP may be required for admin-level accounts only

The dashboard settings row SHALL carry a boolean `totp_required_for_admin_role` (default false, migrated with a server default so existing installs keep today's behaviour). The TOTP policy binding an account SHALL be `totp_required_on_login OR (totp_required_for_admin_role AND is_admin_level(grants))`, evaluated by one predicate that the session dependency, the dashboard-auth management-session checks, the login audit branch and `GET /api/dashboard-auth/session` all use, so the enrolment state reported to the client can never differ from the one enforced on requests. When the policy binds an account that has a secret, a session without TOTP verification MUST be refused with `401 totp_required`; when it binds an account without a secret, the session MUST be issued in the `totp_enrollment_required` state with exactly the self-service routes of "Per-user TOTP with an enrolment state" open. Accounts the policy does not bind (for example an operator or viewer while only the admin-role toggle is on) MUST sign in and work exactly as with both toggles off. `totp_required_for_admin_role` on its own MUST NOT make a passwordless install require a login, and MUST NOT affect the implicit admin principals (local bootstrap, trusted header, disabled auth), which have no account.

#### Scenario: Admin-role requirement binds an admin without a secret

- **GIVEN** `totp_required_for_admin_role` is on and `totp_required_on_login` is off
- **AND** an admin-preset account with a password and no TOTP secret is signed in
- **WHEN** it requests `/api/settings`
- **THEN** the response is `403 totp_enrollment_required`
- **AND** `/api/dashboard-auth/session` answers `200` with `totp_enrollment_required: true`
- **AND** after enrolling, the same session answers `401 totp_required` until the code is verified

#### Scenario: Operator is unaffected

- **GIVEN** the same install
- **WHEN** an operator account without a TOTP secret signs in
- **THEN** the login response and the session report `totp_enrollment_required: false` and `/api/settings` answers `200`

#### Scenario: Custom role holding a privileged permission is bound

- **GIVEN** the same install and an account whose custom role grants `audit:read`
- **WHEN** it requests `/api/dashboard/overview`
- **THEN** the response is `403 totp_enrollment_required`

### Requirement: Enabling a TOTP requirement needs the acting account's own secret

Turning `totp_required_on_login` or `totp_required_for_admin_role` from off to on through `PUT /api/settings` MUST be refused with `400 invalid_totp_config` unless the acting account holds a TOTP secret; the guard MUST read the acting account's secret (the user row), never the legacy `dashboard_settings.totp_secret_encrypted` column, and MUST fire only on the off→on transition so that saving other settings while a requirement is on is not refused for accounts without a secret. Turning `totp_required_on_login` on MUST additionally be refused with `409 compat_user_locked` while the migrated `admin` account is active with a password and no TOTP secret (a previous-release replica reads the legacy columns and would refuse that account forever; this lock is removed with the legacy mirror in the next release); the admin-role toggle is not mirrored and carries no such lock. The settings response SHALL NOT carry a per-account `totpConfigured` (it lives in the session response, which is not cached across accounts); it SHALL report `usersWithoutTotpCount` as the number of active password accounts without a secret and `adminsWithoutTotpCount` as the subset of those that are admin-level. `totp_required_for_admin_role` SHALL be a security field of `PUT /api/settings` and SHALL appear in the `settings_changed` audit details when its value changes. Removing the dashboard password on a single-account install (`DELETE /api/dashboard-auth/password`) SHALL reset both requirements, so the next bootstrap starts with neither.

#### Scenario: Actor without a secret cannot enable either requirement

- **GIVEN** a signed-in admin without a TOTP secret
- **WHEN** it sends `PUT /api/settings` with `totpRequiredForAdminRole: true`, or with `totpRequiredOnLogin: true`
- **THEN** the response is `400 invalid_totp_config` and the setting is unchanged

#### Scenario: Global requirement waits for the compat admin

- **GIVEN** the migrated `admin` account has a password and no TOTP secret, and a second admin has enrolled
- **WHEN** the second admin sends `PUT /api/settings` with `totpRequiredOnLogin: true`
- **THEN** the response is `409 compat_user_locked`
- **AND** `totpRequiredForAdminRole: true` from the same account succeeds
- **AND** once `admin` has enrolled, `totpRequiredOnLogin: true` succeeds

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

## MODIFIED Requirements

### Requirement: Per-user TOTP with an enrolment state

TOTP secrets and replay counters SHALL be stored per account and operated on the signed-in account (`/totp/setup/start` labels the otpauth URI with the username, `/totp/setup/confirm` stores the account's secret, `/totp/verify` and `/totp/disable` check the account's secret and advance its replay counter). The global `totp_required_on_login` setting SHALL mean that every account with a password must complete TOTP; `totp_required_for_admin_role` SHALL mean that every admin-level account must (see "TOTP may be required for admin-level accounts only"). When either requirement binds the account and the account has a secret, a session without `tp` MUST be refused with `401 totp_required`. When a requirement binds the account and the account has no secret, the session MUST be issued in the `totp_enrollment_required` state: `GET /api/dashboard-auth/session` reports `totp_enrollment_required: true`, and only the following self-service routes serve the session: `GET /api/dashboard-auth/session`, `POST /api/dashboard-auth/totp/setup/start`, `POST /api/dashboard-auth/totp/setup/confirm`, `POST /api/dashboard-auth/totp/verify`, `POST /api/dashboard-auth/logout`, `POST /api/dashboard-auth/logout-all`, `GET /api/dashboard-auth/me`, and `POST /api/dashboard-auth/password/change`. Every other dashboard route — including the guest-password, guest logout-all, password-removal and TOTP-disable routes under the same prefix — MUST answer `403 totp_enrollment_required`. Password verification MUST run exactly one password-hash comparison whether or not the named account exists or holds a password, so response timing does not reveal which usernames are real.

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

### Requirement: Sensitive dashboard routes are gated by their own permission

The following routes MUST require the named permission at `all` scope, independent of the generic write gate:

- `POST /api/accounts/{id}/export`, `POST /api/accounts/{id}/export/auth`, `POST /api/accounts/{id}/export/opencode-auth` → `accounts:export`
- `GET /api/audit-logs` → `audit:read`
- `GET /api/conversations`, `GET /api/conversations/`, `GET /api/conversations/{id}`, `GET /api/conversation-archive/files`, `GET /api/conversation-archive/records` → `conversations:read`
- `GET /api/request-logs` with the `conversation_id` filter → `conversations:read`; request-log sensitive metadata (client IP, full user agent, conversation ID, archive lookup ID) MUST be included only for principals holding `conversations:read`
- `GET /api/dashboard/overview`, `GET /api/dashboard/projections`, `GET /api/usage/summary`, `GET /api/usage/history`, `GET /api/usage/window` → `accounts:read`
- `GET /api/models` → `dashboard:read`
- Pure account mutations — `POST /api/accounts/import`, `PATCH /api/accounts/{id}`, `DELETE /api/accounts/{id}`, `POST /api/accounts/{id}/pause`, `POST /api/accounts/{id}/reactivate`, `POST /api/accounts/{id}/probe`, `PUT /api/accounts/{id}/alias`, `PUT /api/accounts/{id}/limit-warmup`, `PUT /api/accounts/{id}/routing-policy`, `POST /api/accounts/{id}/usage-reset-credits/consume`, `POST /api/accounts/{id}/rate-limit-reset-credits/consume`, `POST /api/oauth/start`, `POST /api/oauth/complete`, `POST /api/oauth/manual-callback` → `accounts:write` (in place of the coarse write alias, so a custom role granting `accounts:write` alone can operate accounts; a guest answers `403 permission_required` naming `accounts:write`)
- `POST /api/firewall/ips`, `DELETE /api/firewall/ips/{ip}`, `POST /api/dashboard-auth/guest/password`, `DELETE /api/dashboard-auth/guest/password`, `POST /api/settings/upstream-proxy/endpoints` → `security:write`
- `PUT /api/settings` → `security:write` in addition to the generic write gate when the request body **changes** any of `totp_required_on_login`, `totp_required_for_admin_role`, `api_key_auth_enabled`, `guest_access_enabled`, `dashboard_session_ttl_seconds`, or `hide_upstream_quota_from_api_keys` (a non-null value that differs from the stored setting); requests that re-send the stored values or omit these fields MUST remain authorized by the generic write gate alone, because the dashboard client submits the whole form on every save

The Operator and Viewer presets, created through the invite flow and signed in as accounts, MUST observe this matrix end to end: an Operator's operational requests (account edits, API-key creation and listing, sticky sessions, upstream-proxy administration, non-security settings) succeed while the routes above answer `403 permission_required`; a Viewer's reads of the guest surface succeed with the same account-identity masking guests receive (it lacks `accounts:write`), its reads of inventories and topology answer `403 permission_required`, and every mutation answers `403` on a write-class gate.

#### Scenario: Write-capable principal cannot export credentials

- **WHEN** a principal holding the `write` alias but not `accounts:export` requests `POST /api/accounts/{id}/export/auth`
- **THEN** the response is HTTP 403 with error code `permission_required` and `param` `accounts:export`
- **AND** the same principal may still update the account alias

#### Scenario: Write-capable principal cannot change security settings

- **WHEN** a principal holding the `write` alias but not `security:write` sends `PUT /api/settings` with `apiKeyAuthEnabled` set to a value different from the stored one
- **THEN** the response is HTTP 403 with error code `permission_required` and `param` `security:write`
- **AND** a `PUT /api/settings` from the same principal that sets only `stickyThreadsEnabled` succeeds

#### Scenario: Full-form save with unchanged security fields needs no extra permission

- **WHEN** a principal holding the `write` alias but not `security:write` sends `PUT /api/settings` containing every settings field, with the security fields equal to their stored values and one operational field changed
- **THEN** the request succeeds and the operational change is applied

#### Scenario: Generic write gate runs before the security gate

- **WHEN** a guest principal sends `PUT /api/settings` changing `guestAccessEnabled`
- **THEN** the response is HTTP 403 with error code `read_only_access` and no `param`
- **AND** a principal holding `security:write` but not the `write` alias receives `read_only_access` for the same request

#### Scenario: Account-window projections require account read access

- **WHEN** a principal holding only `dashboard:read` requests `GET /api/dashboard/overview` or `GET /api/usage/summary`
- **THEN** the response is HTTP 403 with error code `permission_required` and `param` `accounts:read`
- **AND** `GET /api/models` succeeds for the same principal

#### Scenario: Invited Operator

- **GIVEN** an admin invited an account with the operator preset and the person accepted the invite
- **WHEN** the operator updates an account alias, creates an API key and saves `stickyThreadsEnabled`
- **THEN** each request succeeds
- **AND** `GET /api/conversations`, `GET /api/audit-logs`, `POST /api/accounts/{id}/export`, `GET /api/dashboard-users`, `POST /api/firewall/ips` and `PUT /api/settings` changing `totpRequiredForAdminRole` answer `403 permission_required` naming the missing permission

#### Scenario: Invited Viewer

- **GIVEN** an admin invited an account with the viewer preset and the person accepted the invite
- **WHEN** the viewer lists accounts
- **THEN** the response is `200` with masked e-mail and no upstream account identifier
- **AND** `GET /api/api-keys` answers `403 permission_required` (`api_keys:read`)
- **AND** `PUT /api/settings` and `PUT /api/accounts/{id}/alias` answer `403 read_only_access`
