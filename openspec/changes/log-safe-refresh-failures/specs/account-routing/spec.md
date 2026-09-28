## ADDED Requirements

### Requirement: Secret-safe refresh failure correlation
The system SHALL log each failed refresh attempt with a stable pseudonymous account reference, an allowlisted failure code, and permanent and transport classification flags. The event SHALL be labeled as a refresh-attempt failure, not proof of an OAuth exchange. Known local pre-exchange failures `upstream_proxy_unavailable` and `refresh_claim_timeout` SHALL retain their safe codes rather than being mapped to `other`. It SHALL NOT log raw account identifiers, credentials, provider messages, response bodies, or exception traces in that diagnostic, including when shared refresh work serves private callers. Unknown codes SHALL be represented as `other`.

#### Scenario: Revoked refresh token
- **WHEN** a refresh exchange fails with `refresh_token_revoked`
- **THEN** the diagnostic includes that code and a stable pseudonymous account reference
- **AND** does not contain the refresh token, account email, raw account ID, or provider error message

#### Scenario: Untrusted provider error code
- **WHEN** an OAuth error code is not allowlisted
- **THEN** the diagnostic logs `other` and never includes the untrusted value

#### Scenario: Local failure before exchange
- **WHEN** route resolution or refresh admission fails before the provider exchange
- **THEN** the refresh-attempt diagnostic preserves `upstream_proxy_unavailable` or `refresh_claim_timeout` respectively
- **AND** no provider call is made

#### Scenario: Private and ordinary callers share a failed refresh
- **WHEN** a private caller and an ordinary caller overlap on the same refresh, in either arrival order
- **THEN** a single refresh attempt runs and both callers receive the refresh error
- **AND** exactly one content-free, account-correlatable failure warning is emitted

#### Scenario: Refresh claim exhausts the caller budget
- **WHEN** a foreign claim remains held until the caller budget expires, or acquisition finishes after that budget expires
- **THEN** exactly one safe refresh-attempt warning includes `code=refresh_claim_timeout`
- **AND** no provider exchange runs, and an acquired claim is released
