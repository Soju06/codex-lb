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

The system SHALL issue dashboard password-authenticated sessions with an absolute lifetime controlled by persisted dashboard settings. The default lifetime SHALL remain 12 hours. The configured lifetime SHALL apply to newly issued dashboard password sessions by setting both the encrypted session expiry payload and the cookie `Max-Age` to the same value.

#### Scenario: Newly issued dashboard password session honors configured lifetime

- **WHEN** an admin configures a dashboard session lifetime and successfully completes password authentication
- **THEN** the newly issued dashboard session expires after the configured absolute lifetime
- **AND** the cookie `Max-Age` matches the same configured lifetime

#### Scenario: Existing dashboard sessions keep their original expiry

- **WHEN** an admin changes the configured dashboard session lifetime after a session cookie has already been issued
- **THEN** previously issued cookies continue to expire according to the expiry embedded in their encrypted payload
- **AND** only newly issued dashboard password sessions use the updated lifetime

