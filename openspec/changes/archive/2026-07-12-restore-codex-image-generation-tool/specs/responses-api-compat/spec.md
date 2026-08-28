## MODIFIED Requirements

### Requirement: Backend Codex Responses preserve advertised image_generation tools

The service MUST accept HTTP and websocket `/backend-api/codex/responses`
request-create payloads that include top-level `tools` entries with
`type: "image_generation"`. During shared Responses validation and upstream
forwarding, the service MUST preserve those top-level `image_generation` tool
entries so Codex clients can expose and use the built-in image-generation
surface. The service MUST also preserve all other tool entries and the existing
built-in tool forwarding policy for public `/v1/*` routes.

#### Scenario: Backend Codex HTTP request preserves advertised image_generation tool

- **WHEN** a client sends `POST /backend-api/codex/responses` with
  `tools=[{"type":"image_generation"},{"type":"function","name":"x"}]`
- **THEN** the request is accepted instead of failing with
  `invalid_request_error`
- **AND** the upstream Responses payload preserves the `image_generation` tool
- **AND** the remaining `function` tool is preserved

#### Scenario: Backend Codex websocket create preserves advertised image_generation tool

- **WHEN** a websocket `response.create` payload for
  `/backend-api/codex/responses` includes a top-level
  `{"type":"image_generation"}` tool entry
- **THEN** the backend Codex websocket request is accepted
- **AND** the forwarded upstream `response.create` payload preserves that
  `image_generation` tool entry

#### Scenario: Public v1 Responses built-in forwarding policy remains unchanged

- **WHEN** a client sends `/v1/responses` with
  `tools=[{"type":"image_generation"}]`
- **THEN** the service does not locally reject the built-in tool as an
  `invalid_request_error`
- **AND** the upstream Responses payload preserves the `image_generation` tool
