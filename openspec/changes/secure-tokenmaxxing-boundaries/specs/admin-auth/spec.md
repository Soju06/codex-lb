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

#### Scenario: Required Access assertion blocks fallback authentication

- **GIVEN** Access JWT validation is configured as required
- **WHEN** a dashboard request has a missing or invalid assertion
- **THEN** the request is rejected before password-session or other fallback dashboard authentication

#### Scenario: Required Access assertion does not block health and read-only internal probes

- **GIVEN** Access JWT validation is configured as required
- **WHEN** a health probe or read-only internal readiness probe lacks an Access assertion
- **THEN** the probe is allowed to reach its route handler
- **AND** unvalidated trusted identity headers are stripped before forwarding
- **AND** mutating internal endpoints still require a valid Access assertion

#### Scenario: Required Access assertion preserves API-key traffic

- **GIVEN** Access JWT validation is configured as required
- **WHEN** an API-key or ChatGPT-token protected Codex usage, fleet, or proxy
  request lacks an Access assertion
- **THEN** Access validation strips unvalidated trusted identity headers
- **AND** the request is allowed to reach its API-key authentication layer
- **AND** dashboard and mutating internal routes still fail closed without a valid Access assertion
