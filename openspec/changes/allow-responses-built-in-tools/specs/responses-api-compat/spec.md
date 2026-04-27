## MODIFIED Requirements

### Requirement: Allow web_search tools and reject unsupported built-ins
The service MUST accept Responses requests that include tools with type `web_search` or `web_search_preview` and MUST normalize `web_search_preview` to `web_search` before forwarding upstream. For other built-in Responses tool types (including `file_search`, `code_interpreter`, `computer_use`, `computer_use_preview`, and `image_generation`), the service MUST accept the request and MUST forward the tool definitions to upstream unchanged except for the documented `web_search_preview` alias. The same behavior MUST apply on HTTP `/v1/responses`, HTTP `/backend-api/codex/responses`, and the WebSocket equivalents that carry `response.create` payloads. Chat Completions tool policy is out of scope for this requirement and remains governed by `chat-completions-compat`.

#### Scenario: web_search_preview tool accepted
- **WHEN** the client sends `tools=[{"type":"web_search_preview"}]`
- **THEN** the service accepts the request and forwards the tool as `web_search`

#### Scenario: built-in Responses tool accepted over HTTP
- **WHEN** the client sends `/v1/responses` or `/backend-api/codex/responses` with a built-in tool such as `image_generation`, `file_search`, `code_interpreter`, `computer_use`, or `computer_use_preview`
- **THEN** the service accepts the request and forwards the tool definition unchanged except for the documented `web_search_preview` alias

#### Scenario: built-in Responses tool accepted over WebSocket
- **WHEN** the client sends a WebSocket `response.create` payload on `/v1/responses` or `/backend-api/codex/responses` with one or more built-in tools
- **THEN** the service accepts the request and forwards the tool definitions unchanged except for the documented `web_search_preview` alias
