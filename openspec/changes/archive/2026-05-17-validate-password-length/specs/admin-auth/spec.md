## ADDED Requirements

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
