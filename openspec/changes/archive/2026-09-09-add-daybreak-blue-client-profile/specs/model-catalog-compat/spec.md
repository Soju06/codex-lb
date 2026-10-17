## ADDED Requirements

### Requirement: Daybreak profile can initialize from the local model catalog

`GET /backend-api/codex/models` MUST validate the proxy API key whenever `X-Codex-LB-Required-Capability` is present, even when deployment-wide API-key authentication is disabled. With a valid key it MUST return the existing local Codex catalog without applying the unsupported-transport denial, selecting an account, or dispatching an upstream request. Headerless model-catalog requests MUST retain their existing behavior.

#### Scenario: Authenticated Daybreak catalog request remains available

- **WHEN** the Daybreak provider requests the Codex-native model catalog with its valid key and capability carrier
- **THEN** the route returns the existing catalog response
- **AND** no account is selected and no upstream request is made

#### Scenario: Catalog carrier requires authentication

- **WHEN** a capability-bearing catalog request omits its proxy API key or supplies an invalid key
- **THEN** the route returns the existing HTTP 401 `invalid_api_key` response
- **AND** no catalog response or routing attempt occurs

#### Scenario: Headerless catalog behavior remains unchanged

- **WHEN** a model-catalog request omits the required-capability carrier
- **THEN** the existing deployment-level authentication and catalog behavior remains in effect
