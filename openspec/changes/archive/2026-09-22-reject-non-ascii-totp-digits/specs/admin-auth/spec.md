## ADDED Requirements

### Requirement: TOTP normalization accepts only ASCII digits

The system SHALL retain only ASCII digits (`0` through `9`) when normalizing
TOTP codes. A normalized code whose length is not six MUST be rejected as an
invalid code without raising a server error. Existing formatting tolerance for
ASCII codes, time-window verification, and replay protection MUST remain intact.

#### Scenario: Unicode digits do not crash setup or verification

- **GIVEN** an eligible password-authenticated session
- **WHEN** TOTP setup confirmation or configured TOTP verification receives six fullwidth or Arabic-Indic digits, or a six-character mixture of ASCII and non-ASCII digits
- **THEN** the endpoint returns HTTP 400 with error code `invalid_totp_code`
- **AND** no TOTP enrollment or replay counter is advanced

#### Scenario: Formatted ASCII code remains valid

- **WHEN** a current, unused ASCII TOTP code contains spaces or hyphens
- **THEN** verification succeeds after existing formatting normalization
