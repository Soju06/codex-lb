## MODIFIED Requirements

### Requirement: Dashboard settings must expose upstream proxy routing controls
The settings dashboard MUST allow operators to inspect upstream proxy routing state, enable or disable routing, choose the default proxy pool, create proxy endpoints, create proxy pools, and add endpoints to pools. For every endpoint the admin API reports as `plaintextCredentials: true`, the endpoint list MUST render a warning stating that its credentials are sent unencrypted over that scheme and recommending an `https://` proxy or a credential-free IP allowlist; endpoints reported as `false` MUST render no such warning.

#### Scenario: Operator creates a pool from existing endpoints
- **GIVEN** the upstream proxy admin API returns at least one endpoint
- **WHEN** an operator creates a pool and selects endpoint members
- **THEN** the dashboard MUST call the pool creation API with the selected endpoint ids
- **AND** refresh the displayed upstream proxy admin state.

#### Scenario: Plaintext-credential endpoint shows a warning

- **GIVEN** the upstream proxy admin API returns an endpoint with `plaintextCredentials: true`
- **WHEN** the upstream proxy settings section renders
- **THEN** that endpoint's row shows the plaintext-credential warning naming its scheme

#### Scenario: Encrypted or credential-free endpoint shows no warning

- **GIVEN** the upstream proxy admin API returns an endpoint with `plaintextCredentials: false`
- **WHEN** the upstream proxy settings section renders
- **THEN** that endpoint's row shows no plaintext-credential warning
