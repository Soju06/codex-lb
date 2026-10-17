## ADDED Requirements

### Requirement: Dashboard authorization uses resource-scoped permissions

The system SHALL express dashboard authorization as a set of fine-grained permissions named `<resource>:<action>`, each granted with a scope of `all` or `own`. The vocabulary MUST be exactly: `dashboard:read`, `accounts:read`, `accounts:write`, `accounts:export`, `api_keys:read`, `api_keys:write`, `api_keys:assign`, `ops:write`, `security:write`, `users:manage`, `roles:manage`, `conversations:read`, `audit:read`. Only `dashboard:read`, `api_keys:read`, and `api_keys:write` MAY be granted with `own` scope; every other permission is all-or-nothing. A grant of `all` scope MUST satisfy a requirement for `own` scope; a grant of `own` scope MUST NOT satisfy a requirement for `all` scope.

The system MUST enforce these dependency rules on every grant table it accepts: `api_keys:assign` requires `api_keys:write` at `all` scope, `accounts:export` requires `accounts:read` at `all` scope, and `security:write` requires `ops:write` at `all` scope. A grant table that violates a dependency rule or grants `own` scope to an all-or-nothing permission MUST be rejected before it is used.

#### Scenario: Built-in role grants are validated at startup

- **WHEN** the application imports the dashboard access module
- **THEN** every built-in role grant table passes the scope and dependency rules
- **AND** a table granting `security:write` without `ops:write`, or granting `security:write` with `own` scope, is rejected with an error

#### Scenario: All scope satisfies own scope

- **WHEN** a principal holds `api_keys:read` at `all` scope
- **THEN** it satisfies a requirement for `api_keys:read` at `own` scope
- **AND** a principal holding `api_keys:read` at `own` scope does not satisfy a requirement at `all` scope

### Requirement: Built-in roles map to fixed grant tables

The `admin` role SHALL hold every permission at `all` scope. The `guest` role SHALL hold exactly `dashboard:read` and `accounts:read` at `all` scope. The coarse `read` and `write` permissions exposed in the dashboard session response MUST be derived from the grant table: `read` is present when `dashboard:read` is granted at any scope, and `write` is present only when `accounts:write`, `api_keys:write`, and `ops:write` are all granted at `all` scope. The session response MUST continue to expose only `read` and `write` in its `permissions` list.

#### Scenario: Admin session exposes both aliases

- **WHEN** an admin principal is issued
- **THEN** its grants contain every permission at `all` scope
- **AND** the session response `permissions` list contains exactly `read` and `write`

#### Scenario: Guest session exposes only read

- **WHEN** a guest principal is issued
- **THEN** its grants contain exactly `dashboard:read` and `accounts:read`
- **AND** the session response `permissions` list contains exactly `read`

#### Scenario: Partial writer does not receive the write alias

- **WHEN** a principal is granted `accounts:write` and `api_keys:write` at `all` scope but not `ops:write`
- **THEN** its derived permissions contain `read` but not `write`
- **AND** the generic dashboard write gate rejects it with `read_only_access`

### Requirement: Permission denials name the missing permission

When an authenticated dashboard principal lacks a permission a route requires, the system MUST return HTTP 403 with error code `permission_required` and MUST set the error envelope `param` field to the missing permission name. The dashboard error envelope MAY carry an optional `param` string; it MUST be omitted when no parameter applies. The generic write gate MUST continue to return `read_only_access` for principals without the `write` alias.

#### Scenario: Missing specific permission

- **WHEN** a principal without `audit:read` requests `GET /api/audit-logs`
- **THEN** the response is HTTP 403
- **AND** the body is `{"error": {"code": "permission_required", "message": ..., "param": "audit:read"}}`

#### Scenario: Read-only principal on a generic mutation

- **WHEN** a guest principal requests a mutating endpoint gated by the generic write gate
- **THEN** the response is HTTP 403 with error code `read_only_access`

### Requirement: Sensitive dashboard routes are gated by their own permission

The following routes MUST require the named permission at `all` scope, independent of the generic write gate:

- `POST /api/accounts/{id}/export`, `POST /api/accounts/{id}/export/auth`, `POST /api/accounts/{id}/export/opencode-auth` → `accounts:export`
- `GET /api/audit-logs` → `audit:read`
- `GET /api/conversations`, `GET /api/conversations/`, `GET /api/conversations/{id}`, `GET /api/conversation-archive/files`, `GET /api/conversation-archive/records` → `conversations:read`
- `GET /api/request-logs` with the `conversation_id` filter → `conversations:read`; request-log sensitive metadata (client IP, full user agent, conversation ID, archive lookup ID) MUST be included only for principals holding `conversations:read`
- `GET /api/dashboard/overview`, `GET /api/dashboard/projections`, `GET /api/usage/summary`, `GET /api/usage/history`, `GET /api/usage/window` → `accounts:read`
- `GET /api/models` → `dashboard:read`
- `POST /api/firewall/ips`, `DELETE /api/firewall/ips/{ip}`, `POST /api/dashboard-auth/guest/password`, `DELETE /api/dashboard-auth/guest/password`, `POST /api/settings/upstream-proxy/endpoints` → `security:write`
- `PUT /api/settings` → `security:write` in addition to the generic write gate when the request body **changes** any of `totp_required_on_login`, `api_key_auth_enabled`, `guest_access_enabled`, `dashboard_session_ttl_seconds`, or `hide_upstream_quota_from_api_keys` (a non-null value that differs from the stored setting); requests that re-send the stored values or omit these fields MUST remain authorized by the generic write gate alone, because the dashboard client submits the whole form on every save

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

### Requirement: Dashboard route authorization is machine-verified

The test suite MUST walk the live application route table and fail when: (a) any route under `/api/` other than `/api/fleet/*`, `/api/codex/*`, and the session-issuing `/api/dashboard-auth/*` endpoints lacks a dashboard session gate in its dependency tree; (b) any such route with a non-safe HTTP method lacks the generic write gate or a permission requirement whose permission is not a `*:read` permission; (c) any route listed in the previous requirement whose requirement is unconditional no longer carries its named permission in its dependency tree (request-conditional checks — the `PUT /api/settings` security fields and the request-log `conversation_id` filter — are covered by behavioral tests instead); or (d) any route requires `own` scope while no ownership model exists. The guest-password mutations under `/api/dashboard-auth/` MUST be checked for `security:write` despite the module exemption.

#### Scenario: New unguarded endpoint fails CI

- **WHEN** a dashboard router adds a `POST` route whose dependency tree contains neither the generic write gate nor a non-read permission requirement
- **THEN** the route authorization matrix test fails naming that method and path

#### Scenario: Sensitive route regression fails CI

- **WHEN** `GET /api/audit-logs` is changed to require only a dashboard session
- **THEN** the route authorization matrix test fails naming the route and its current requirements

## MODIFIED Requirements

### Requirement: Dashboard guest access is read-only

The system SHALL support a dashboard `guest` role with read permission and without write permission. Among the fine-grained permissions the `guest` role holds exactly `dashboard:read` and `accounts:read`; full conversation archives and request-log filtering by a dedicated conversation identifier MUST require the `conversations:read` permission, which the guest role does not hold. The system SHALL continue to treat password-authenticated, trusted-header, disabled-auth, and local bootstrap users as `admin` principals holding every permission.

#### Scenario: Guest can read dashboard APIs

- **WHEN** guest access is enabled and a guest principal requests a guest-safe dashboard GET endpoint
- **THEN** the request succeeds using read-only dashboard access
- **AND** the session response identifies the principal as `guest`
- **AND** the session response includes only the `read` permission

#### Scenario: Guest cannot read conversation archives

- **WHEN** a guest principal requests any conversation-archive endpoint
- **THEN** the system returns HTTP 403 with error code `permission_required` and `param` `conversations:read`
- **AND** no archive file metadata, payload, headers, or other archive record data is returned

#### Scenario: Guest cannot filter request logs by conversation identifier

- **WHEN** a guest principal requests request logs with the dedicated `conversation_id` filter
- **THEN** the system returns HTTP 403 with error code `permission_required` and `param` `conversations:read`
- **AND** no filtered rows, request count, or aggregated conversation cost is returned

#### Scenario: Guest cannot mutate dashboard state

- **WHEN** guest access is enabled and a guest principal requests a dashboard mutating endpoint gated only by the generic write gate
- **THEN** the system returns HTTP 403 with error code `read_only_access`
- **AND** no dashboard state is changed

#### Scenario: Guest on a permission-gated mutation

- **WHEN** a guest principal requests a mutating endpoint gated by a specific permission (firewall rules, guest password, upstream-proxy endpoint creation, account credential export)
- **THEN** the system returns HTTP 403 with error code `permission_required` and `param` naming the missing permission
- **AND** no dashboard state is changed

### Requirement: Guest access may be enabled without a guest password

The system SHALL allow operators to enable guest access without configuring a guest password. When guest access is enabled and no guest password is configured, remote dashboard requests that do not have an admin session SHALL be authorized as a `guest` principal for read-only routes.

#### Scenario: Passwordless guest reads remotely

- **WHEN** guest access is enabled
- **AND** no guest password is configured
- **AND** a remote request has no admin dashboard session
- **THEN** dashboard GET endpoints treat the request as a `guest`

#### Scenario: Passwordless guest still cannot write

- **WHEN** guest access is enabled without a guest password
- **AND** a remote request has no admin dashboard session
- **THEN** dashboard mutating endpoints return HTTP 403 with error code `read_only_access` when gated only by the generic write gate, or `permission_required` when gated by a specific permission

### Requirement: Guest access may require a guest password

The system SHALL allow operators to configure a separate guest password. When guest access is enabled and a guest password is configured, unauthenticated remote dashboard requests SHALL remain blocked until the guest password login endpoint issues a guest session.

#### Scenario: Password-protected guest login succeeds

- **WHEN** guest access is enabled with a guest password
- **AND** a remote client submits the correct guest password
- **THEN** the system issues a dashboard session with role `guest`
- **AND** subsequent dashboard GET endpoints are allowed

#### Scenario: Password-protected guest write is denied

- **WHEN** a password-authenticated guest session requests a dashboard mutating endpoint
- **THEN** the system returns HTTP 403 with error code `read_only_access` when gated only by the generic write gate, or `permission_required` when gated by a specific permission

### Requirement: Guest conversation reads require an admin principal

Conversation list and detail routes are not guest-safe dashboard reads and MUST
require the `conversations:read` permission. The requirement is expressed as a
permission rather than a role; among the built-in roles only `admin` holds it.
Existing guest-safe GET behavior and the existing read-only write restrictions
SHALL remain unchanged.

#### Scenario: Guest cannot read conversations

- **WHEN** a guest principal requests `GET /api/conversations`, `GET /api/conversations/`, or `GET /api/conversations/{id}`
- **THEN** the system returns HTTP 403 with error code `permission_required` and `param` `conversations:read`
- **AND** no conversation list or detail payload is returned

#### Scenario: Admin can read conversations

- **WHEN** an admin principal requests a conversation list or detail route
- **THEN** the request succeeds with the existing conversation response contract
