## ADDED Requirements

### Requirement: Backend Codex Responses tolerate advertised image_generation tools
The service MUST accept HTTP and websocket `/backend-api/codex/responses`
request-create payloads that include top-level `tools` entries with
`type: "image_generation"`. Before shared Responses validation and upstream
forwarding, the service MUST remove only those advertised top-level
`image_generation` tool entries while preserving all other tool entries.

#### Scenario: Backend Codex HTTP request strips advertised image_generation tool
- **WHEN** a client sends `POST /backend-api/codex/responses` with
  `tools=[{"type":"image_generation"},{"type":"function","name":"x"}]`
- **THEN** the request is accepted instead of failing with
  `invalid_request_error`
- **AND** the upstream Responses payload omits the `image_generation` tool
- **AND** the remaining `function` tool is preserved

#### Scenario: Backend Codex websocket create strips advertised image_generation tool
- **WHEN** a websocket `response.create` payload for
  `/backend-api/codex/responses` includes a top-level
  `{"type":"image_generation"}` tool entry
- **THEN** the backend Codex websocket request is accepted
- **AND** the forwarded upstream `response.create` payload omits that
  `image_generation` tool entry
