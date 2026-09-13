## ADDED Requirements

### Requirement: Audit rows record the acting account, auth method, target and severity

Every `audit_logs` row MUST carry nullable `actor_user_id`, `actor_username`, `actor_role_slug`, `auth_method`, `target_type`, `target_id` columns and a NOT NULL `severity` column (`info`, `warning`, or `critical`; default `info`). The actor columns MUST be a snapshot taken at write time with no foreign key to `dashboard_users`, so a row survives the deletion of the account it names. An audit write initiated by a dashboard request MUST record the request principal: an account session records the account's id, username, role slug and authentication method; the implicit admin (local bootstrap, trusted header, disabled auth) records `actor_role_slug` `admin`, its `auth_method` (`local_bootstrap`, `trusted_header`, or `disabled`), a NULL id, and as `actor_username` the proxy-asserted username when the request carried one (trusted header) and NULL otherwise; a guest records `actor_role_slug` `guest` and `auth_method` `guest`. Where the acted-on object is known the write MUST record `target_type` and `target_id` (for example `account`, `api_key`, `model_source`, `user`, or `settings` with the settings group as id). Rows written before this requirement existed MUST keep NULL actor and target columns and MUST read as `severity` `info`. `AuditService.log_async()` and `AuditService.log()` MUST keep accepting their existing positional parameters unchanged and accept the actor, target and severity as keyword-only parameters.

#### Scenario: Signed-in account changes settings

- **GIVEN** an `admin` account signed in with a password
- **WHEN** it calls `PUT /api/settings`
- **THEN** the `settings_changed` row records that account's id, username `admin`, role slug `admin`, `auth_method` `password`
- **AND** `target_type` `settings` with `target_id` `dashboard`
- **AND** `severity` `info`

#### Scenario: Implicit local admin is recorded without an account

- **GIVEN** a passwordless install serving a local request as the implicit admin
- **WHEN** the request performs an audited mutation
- **THEN** the row records `auth_method` `local_bootstrap` and `actor_role_slug` `admin`
- **AND** `actor_user_id` and `actor_username` are NULL

#### Scenario: Trusted-header admin records the asserted username

- **GIVEN** `dashboard_auth_mode` `trusted_header` and a request whose trusted header names `ops`
- **WHEN** the request performs an audited mutation
- **THEN** the row records `auth_method` `trusted_header`, `actor_role_slug` `admin`, `actor_username` `ops`
- **AND** `actor_user_id` is NULL

#### Scenario: Pre-existing rows are unchanged by the migration

- **GIVEN** an `audit_logs` row written before revision `20260909_030000_add_audit_actor_columns`
- **WHEN** the revision is applied
- **THEN** the row's actor and target columns are NULL and its `severity` is `info`
- **AND** downgrading the revision removes the new columns and indexes while keeping the row

### Requirement: Audit writes fan out to registered sinks; the database sink is authoritative

The system MUST build one `AuditEvent` per `AuditService.log_async()` / `log()` call, containing the action, a UTC timestamp, the actor, actor IP, target, severity, sanitised details and request id, and MUST deliver it to every registered `AuditSink` in registration order. The database sink MUST be registered first and MUST remain the authoritative record. A sink that raises MUST be reported with the sink's class name and the action, and MUST NOT prevent delivery to the remaining sinks or fail the audit task. With no sink registered by the operator the registry MUST contain exactly the database sink. Sink fan-out MUST NOT change the task-ownership or shutdown-drain guarantees of the asynchronous write path.

#### Scenario: Failing sink does not lose the database row

- **GIVEN** a registered sink that raises on every `emit`
- **WHEN** an audit write runs
- **THEN** the `audit_logs` row is written
- **AND** every other registered sink receives the same event
- **AND** the failure is reported with the sink class and the action

#### Scenario: Details are sanitised before any sink sees them

- **WHEN** an audit write carries details containing a redacted key such as `password`
- **THEN** the event delivered to every sink omits that key
- **AND** the stored row omits it as well

### Requirement: Audit log listing supports actor, target, severity, reason and time filters

`GET /api/audit-logs` (permission `audit:read`) MUST accept, in addition to `action`, `limit` and `offset`, the optional query parameters `actor_user_id`, `target_type`, `target_id`, `severity` (one of `info`, `warning`, `critical`), `reason` (matching `^[a-z_]{1,32}$`, matched against `details.reason`), `since` (inclusive) and `until` (exclusive) as ISO 8601 datetimes normalised to UTC. A value outside these constraints MUST be refused with `422`. Filters combine with AND. Each returned row MUST include `actor` (`{userId, username, roleSlug, authMethod}`, or `null` when every actor column is NULL), `target` (`{type, id}` or `null`) and `severity`. The listing MUST remain read-only: no endpoint updates or deletes audit rows.

#### Scenario: Filter by actor and target

- **GIVEN** rows attributed to account `u-1` acting on `account` `acc-1` and rows without attribution
- **WHEN** `GET /api/audit-logs?actor_user_id=u-1` or `GET /api/audit-logs?target_type=account&target_id=acc-1` is called
- **THEN** only the attributed rows are returned
- **AND** each carries `actor.userId` `u-1` and `target` `{type: account, id: acc-1}`

#### Scenario: Filter by reason and time window

- **GIVEN** `login_failed` rows with `details.reason` `bad_password` and `bad_totp` at different times
- **WHEN** `GET /api/audit-logs?reason=bad_totp` is called
- **THEN** only the `bad_totp` row is returned
- **AND** `since` / `until` restrict the result to rows with `since <= timestamp < until`

#### Scenario: Legacy row shape

- **GIVEN** a row whose actor and target columns are NULL
- **WHEN** it is listed
- **THEN** its `actor` and `target` are `null` and its `severity` is `info`

#### Scenario: Invalid filter values are refused

- **WHEN** `reason` contains characters outside `[a-z_]`, `severity` is not in the vocabulary, or `since` is not a datetime
- **THEN** the response is `422`

### Requirement: Failed sign-ins record a reason

Every `login_failed` row MUST have NULL actor columns, `severity` `warning`, and `details` containing `method`, `username` (the normalised username when it is well formed, otherwise `null`) and `reason`, where `reason` is one of `bad_password`, `bad_totp`, `unknown_identity`, `disabled_user`, `invalid_username`, `username_required`. A password login refused with `422 username_required` MUST write a `login_failed` row with that reason unless the client is already at the password rate limit or has exhausted a per-client budget of 8 such rows per 60 seconds; the refusal MUST NOT increment the password rate-limit counter. Recording the reason MUST NOT change what the client receives: unknown, disabled and wrong-password failures keep the identical `401 invalid_credentials` body and single password check. A successful sign-in (`login_success`) MUST record the signed-in account as the actor with the method that completed the sign-in (`password` or `totp`) and the account as `target` (`user`, account id).

#### Scenario: Unknown and disabled accounts are distinguishable in the audit log only

- **GIVEN** an active account `admin`, a disabled account `ghost`, and no account `nobody`
- **WHEN** logins are attempted with a wrong password for `admin`, and any password for `nobody` and `ghost`
- **THEN** each response is `401 invalid_credentials`
- **AND** the `login_failed` rows record reasons `bad_password`, `unknown_identity`, `disabled_user` respectively with `severity` `warning` and NULL actor columns

#### Scenario: Missing username on a multi-account install is audited

- **GIVEN** two active accounts hold passwords
- **WHEN** `POST /api/dashboard-auth/password/login` is called without a username
- **THEN** the response is `422 username_required`
- **AND** a `login_failed` row with `reason` `username_required` and `severity` `warning` is written

#### Scenario: Anonymous refusals cannot flood the audit log

- **GIVEN** two active accounts hold passwords
- **WHEN** one client sends more than 8 username-less login requests within 60 seconds
- **THEN** every response is `422 username_required`
- **AND** at most 8 `login_failed` rows with `reason` `username_required` are written
- **AND** the password rate-limit counter for that client stays at zero

#### Scenario: Successful sign-in names the account

- **WHEN** an account signs in with a password (no TOTP step pending)
- **THEN** the `login_success` row records the account's id, username, role slug and `auth_method` `password`
- **AND** `target_type` `user` with the account id
