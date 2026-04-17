## ADDED Requirements

### Requirement: Responses-family endpoints allow built-in tool definitions

The service MUST accept built-in Responses tool definitions on Responses-family endpoints, including `/v1/responses`, `/backend-api/codex/responses`, and websocket `response.create` payloads. The service MUST continue normalizing `web_search_preview` to `web_search`, but it MUST not reject built-in Responses tools like `file_search`, `code_interpreter`, `computer_use`, `computer_use_preview`, or `image_generation` during local payload validation.

#### Scenario: HTTP Responses request includes image generation

- **WHEN** a client sends a valid Responses request with `tools=[{"type":"image_generation", ...}]`
- **THEN** the request is accepted
- **AND** the mapped payload forwarded upstream preserves the built-in tool definition

#### Scenario: Websocket response.create includes mixed built-in tools

- **WHEN** a client sends websocket `response.create` with built-in tools and `web_search_preview`
- **THEN** the request is accepted
- **AND** `web_search_preview` is normalized to `web_search`
- **AND** the remaining built-in tool definitions are forwarded unchanged
