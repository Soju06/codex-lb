## ADDED Requirements

### Requirement: Plaintext proxy credentials are surfaced, not blocked

An upstream proxy endpoint whose scheme is `http`, `socks5`, or `socks5h` and which has a username or password MUST be accepted by endpoint creation and by route resolution. The upstream proxy admin API MUST report `plaintextCredentials: true` for such an endpoint and `false` for an `https` endpoint or a credential-free endpoint, and MUST NOT include the password in any response. Route resolution MUST emit exactly one warning per such endpoint per process, identifying the endpoint by id, scheme, host, and port without the credential.

#### Scenario: Operator creates a credentialed http proxy endpoint

- **WHEN** an operator creates an endpoint with scheme `http` and a username and password
- **THEN** the request succeeds
- **AND** the created endpoint and the admin listing report `plaintextCredentials: true`

#### Scenario: Credentialed https endpoint is not flagged

- **WHEN** an operator creates an endpoint with scheme `https` and a username and password
- **THEN** the created endpoint reports `plaintextCredentials: false`

#### Scenario: Resolver warns once per endpoint

- **GIVEN** a credentialed `http` endpoint
- **WHEN** it is resolved twice in the same process
- **THEN** exactly one warning is logged for it
- **AND** the warning contains neither the username nor the password
