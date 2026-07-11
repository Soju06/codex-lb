## ADDED Requirements

### Requirement: Trusted dashboard identity is cryptographically validated

When Access JWT validation is configured, the system MUST validate the
`Cf-Access-Jwt-Assertion` signature against the configured issuer JWKS and MUST
validate the exact issuer, configured audience, expiry, and allowed email
domain. The dashboard actor MUST be the normalized email claim from the
validated token and MUST NOT be taken from a separate identity header.

#### Scenario: Valid Access assertion authenticates an employee

- **WHEN** a request arrives from a trusted proxy with a valid Access assertion
- **AND** its issuer and audience exactly match configuration
- **AND** its email belongs to an allowed domain
- **THEN** trusted-header dashboard authentication succeeds
- **AND** the actor is the normalized validated email

#### Scenario: Invalid Access assertion fails closed

- **WHEN** the assertion is missing, forged, expired, has the wrong issuer or
  audience, has a disallowed email domain, or its signing keys cannot be loaded
- **THEN** trusted-header dashboard authentication does not succeed
- **AND** an unvalidated identity header cannot authenticate the request
