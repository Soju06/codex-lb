## ADDED Requirements

### Requirement: Daybreak capability intent fails closed before file routing

`POST /backend-api/files` and `POST /backend-api/files/{file_id}/uploaded` MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present, even when deployment-wide API-key authentication is disabled. After authentication they MUST return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before usage reservation, account selection, upload registration, status polling, or upstream dispatch. Headerless file requests MUST retain their existing behavior.

#### Scenario: Authenticated carrier is denied before file account selection

- **WHEN** a valid proxy API key sends a file-create or file-finalize request with the Daybreak carrier
- **THEN** the route returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no reservation, account selection, upload registration, polling loop, or upstream request begins

#### Scenario: Headerless file behavior remains unchanged

- **WHEN** a file-create or file-finalize request omits the required-capability carrier
- **THEN** the existing authentication, validation, reservation, routing, and response behavior remains in effect
