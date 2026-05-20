## ADDED Requirements

### Requirement: Native /backend-api/codex routes accept the Codex tool surface
The service MUST accept native /backend-api/codex/responses HTTP and websocket requests that include the Codex Desktop tool surface. This includes custom tools plus built-in Codex tool types such as image_generation, file_search, code_interpreter, and computer_use_preview. The service MUST continue normalizing web_search_preview to web_search before forwarding upstream. OpenAI-style /v1/* routes MUST keep rejecting unsupported built-in tools with an invalid_request_error.

#### Scenario: backend responses accept native built-in and custom tools
- **WHEN** a client sends /backend-api/codex/responses with tools including custom exec and image_generation
- **THEN** the service accepts the request and forwards the tools upstream without returning an invalid_request_error

#### Scenario: v1 responses continue rejecting unsupported built-in tools
- **WHEN** a client sends /v1/responses with image_generation in tools
- **THEN** the service returns a 4xx OpenAI invalid_request_error indicating the unsupported tool type
