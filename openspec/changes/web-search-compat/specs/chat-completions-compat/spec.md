## ADDED Requirements

### Requirement: Allow web_search tools in Chat Completions
The service MUST accept Chat Completions requests that include tools with type `web_search` or `web_search_preview`. The service MUST normalize `web_search_preview` to `web_search` when mapping to the Responses tool schema. The service MUST continue to reject other built-in tool types (file_search, code_interpreter, computer_use, computer_use_preview, image_generation) with an OpenAI invalid_request_error.

#### Scenario: web_search_preview tool normalized in mapping
- **WHEN** the client sends `tools=[{"type":"web_search_preview"}]`
- **THEN** the mapped Responses request includes a tool with type `web_search`

#### Scenario: unsupported built-in tool rejected
- **WHEN** the client sends `tools=[{"type":"image_generation"}]`
- **THEN** the service returns a 4xx response with an OpenAI invalid_request_error indicating the unsupported tool type
