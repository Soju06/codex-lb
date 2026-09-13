## ADDED Requirements

### Requirement: Daybreak capability intent bypasses ordinary opportunistic admission

`GET /backend-api/codex/opportunistic/admission` MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present and MUST then return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before model-source or ordinary account-capacity evaluation. Headerless admission requests MUST retain their existing behavior.

#### Scenario: Authenticated carrier is denied before admission evaluation

- **WHEN** a valid proxy API key requests opportunistic admission with the Daybreak carrier
- **THEN** the route returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no model source or ordinary account capacity is evaluated

#### Scenario: Headerless admission behavior remains unchanged

- **WHEN** an opportunistic admission request omits the required-capability carrier
- **THEN** the existing admission policy remains in effect
