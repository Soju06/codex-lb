## MODIFIED Requirements

### Requirement: Native `/backend-api/codex` routes accept the Codex tool surface
The service MUST accept native `/backend-api/codex/responses` HTTP and websocket requests that include the Codex Desktop tool surface. This includes custom tools plus built-in Codex tool types such as `image_generation`, `file_search`, `code_interpreter`, and `computer_use_preview`. The service MUST continue normalizing `web_search_preview` to `web_search` before forwarding upstream. OpenAI-style `/v1/*` routes MUST keep rejecting unsupported built-in tools with an `invalid_request_error`.

#### Scenario: Auto transport prefers HTTP for image-generation tool requests
- **WHEN** the resolved upstream transport strategy is `"auto"`
- **AND** the request includes a built-in `image_generation` tool
- **THEN** the proxy chooses the upstream HTTP/SSE transport even if the model would otherwise prefer websocket
