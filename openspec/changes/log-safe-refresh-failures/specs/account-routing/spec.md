## ADDED Requirements

### Requirement: Secret-safe refresh failure correlation
The system SHALL log each failed OAuth exchange with a stable anonymous account reference, an allowlisted failure code, and permanent and transport classification flags. It SHALL NOT log raw account identifiers, credentials, provider messages, response bodies, or exception traces in that diagnostic, including when shared refresh work serves private callers. Unknown codes SHALL be represented as `other`.

#### Scenario: Revoked refresh token
- **WHEN** a refresh exchange fails with `refresh_token_revoked`
- **THEN** the diagnostic includes that code and a stable anonymous account reference
- **AND** does not contain the refresh token, account email, raw account ID, or provider error message

#### Scenario: Untrusted provider error code
- **WHEN** an OAuth error code is not allowlisted
- **THEN** the diagnostic logs `other` and never includes the untrusted value
