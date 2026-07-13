# admin-auth Specification

## Purpose

Define dashboard authentication behavior so login, bootstrap, TOTP, and session handling stay secure and predictable.
## Requirements
### Requirement: Login rate limiting

The system SHALL rate-limit failed password login attempts using the existing `TotpRateLimiter` pattern: maximum 8 failures per 60-second window. On rate limit breach, the system MUST return 429 with a `Retry-After` header. Requests rejected because password login is not configured MUST NOT consume that failed-login budget.

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

### Requirement: Password length is bounded by bcrypt's input limit

The system SHALL enforce both a minimum and a maximum length on dashboard passwords submitted to `POST /api/dashboard-auth/password/setup` and to the `new_password` field of `POST /api/dashboard-auth/password/change`. The maximum length MUST be measured against the UTF-8 encoded byte length of the password (matching bcrypt's internal limit), not against the codepoint count, and MUST be set to exactly 72 bytes.

#### Scenario: Setup rejects passwords longer than 72 bytes

- **WHEN** `POST /api/dashboard-auth/password/setup` receives a password whose UTF-8 encoded length exceeds 72 bytes
- **THEN** the system returns HTTP 422 with error code `password_too_long`
- **AND** the response message references the 72-byte limit so the client can render it

#### Scenario: Setup accepts passwords up to 72 bytes inclusive

- **WHEN** `POST /api/dashboard-auth/password/setup` receives a password whose UTF-8 encoded length is exactly 72 bytes
- **THEN** the system accepts the password and configures it

#### Scenario: Length is measured in UTF-8 bytes, not codepoints

- **WHEN** `POST /api/dashboard-auth/password/setup` receives a password whose codepoint count is below 72 but whose UTF-8 encoded length exceeds 72 bytes (e.g. an emoji-only string)
- **THEN** the system returns HTTP 422 with error code `password_too_long`

#### Scenario: Change applies the same upper bound to the new password

- **WHEN** `POST /api/dashboard-auth/password/change` receives a `new_password` whose UTF-8 encoded length exceeds 72 bytes
- **THEN** the system returns HTTP 422 with error code `password_too_long` before attempting to hash the password

### Requirement: Dashboard password sessions use a configurable absolute lifetime

The system SHALL issue dashboard password-authenticated sessions with an absolute lifetime controlled by persisted dashboard settings. The default persisted lifetime SHALL be 1 year. Configured lifetimes at or below 30 days SHALL apply to newly issued dashboard password sessions by setting both the encrypted session expiry payload and the cookie `Max-Age` to the same value. Configured lifetimes above 30 days SHALL apply only in standard dashboard auth mode when the request is socket-level local, or when an explicit loopback-host-header override is enabled and the request uses a loopback dashboard URL with no forwarded-client headers. Non-loopback, proxy-aware, trusted-header, or bridge-without-override requests MUST receive a 12-hour effective lifetime without rewriting the persisted setting.

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
- **AND** no forwarded-client headers are present
- **THEN** the newly issued dashboard session expires after the configured absolute lifetime

#### Scenario: Long dashboard password session falls back for non-loopback access

- **WHEN** an admin configures a dashboard session lifetime greater than 30 days and successfully completes password authentication from a non-loopback, proxy-aware, or trusted-header request
- **THEN** the newly issued dashboard session expires after 12 hours
- **AND** the cookie `Max-Age` is `43200`

#### Scenario: Existing dashboard sessions keep their original expiry

- **WHEN** an admin changes the configured dashboard session lifetime after a session cookie has already been issued
- **THEN** previously issued cookies continue to expire according to the expiry embedded in their encrypted payload
- **AND** only newly issued dashboard password sessions use the updated lifetime

### Requirement: Dashboard OAuth callback errors hide internal exception details

Dashboard OAuth manual-callback responses MUST NOT include raw unexpected
exception strings, stack traces, local file paths, or other internal diagnostic
text in the response body. The server MAY log unexpected exceptions for operator
troubleshooting. User-actionable OAuth provider errors MAY continue to expose the
explicit provider-facing error code/message.

#### Scenario: Unexpected manual callback exception is sanitized

- **GIVEN** a dashboard session is authorized
- **AND** the OAuth manual-callback service raises an unexpected exception whose
  message contains internal diagnostic text
- **WHEN** the client calls `POST /api/oauth/manual-callback`
- **THEN** the response returns HTTP 500 with error code `manual_callback_failed`
- **AND** the response message is a generic internal-error message
- **AND** the response body does not contain the raw exception text

#### Scenario: OAuth provider error remains user-actionable

- **GIVEN** a dashboard session is authorized
- **AND** the OAuth manual-callback service raises `OAuthError` with an explicit
  error code and message
- **WHEN** the client calls `POST /api/oauth/manual-callback`
- **THEN** the response exposes that OAuth error code and message to the client

### Requirement: Dashboard guest access is read-only

The system SHALL support a dashboard `guest` role with read permission and without write permission. The system SHALL continue to treat password-authenticated, trusted-header, disabled-auth, and local bootstrap users as `admin` principals with read and write permissions.

#### Scenario: Guest can read dashboard APIs

- **WHEN** guest access is enabled and a guest principal requests a dashboard GET endpoint
- **THEN** the request succeeds using read-only dashboard access
- **AND** the session response identifies the principal as `guest`
- **AND** the session response includes only the `read` permission

#### Scenario: Guest cannot mutate dashboard state

- **WHEN** guest access is enabled and a guest principal requests a dashboard mutating endpoint
- **THEN** the system returns HTTP 403 with error code `read_only_access`
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
- **THEN** dashboard mutating endpoints return HTTP 403 with error code `read_only_access`

### Requirement: Guest access may require a guest password

The system SHALL allow operators to configure a separate guest password. When guest access is enabled and a guest password is configured, unauthenticated remote dashboard requests SHALL remain blocked until the guest password login endpoint issues a guest session.

#### Scenario: Password-protected guest login succeeds

- **WHEN** guest access is enabled with a guest password
- **AND** a remote client submits the correct guest password
- **THEN** the system issues a dashboard session with role `guest`
- **AND** subsequent dashboard GET endpoints are allowed

#### Scenario: Password-protected guest write is denied

- **WHEN** a password-authenticated guest session requests a dashboard mutating endpoint
- **THEN** the system returns HTTP 403 with error code `read_only_access`

### Requirement: Legacy default dashboard session TTL migration

The migration for this change MUST update `dashboard_settings.dashboard_session_ttl_seconds` from `43200` to `31536000` only for rows that still carry the legacy default value. Rows with any customized value MUST remain unchanged.

#### Scenario: Legacy default row migrates to 1 year

- **GIVEN** a dashboard settings row has `dashboard_session_ttl_seconds = 43200`
- **WHEN** the migration runs
- **THEN** the row has `dashboard_session_ttl_seconds = 31536000`

#### Scenario: Customized row remains unchanged

- **GIVEN** a dashboard settings row has `dashboard_session_ttl_seconds = 7200`
- **WHEN** the migration runs
- **THEN** the row still has `dashboard_session_ttl_seconds = 7200`

