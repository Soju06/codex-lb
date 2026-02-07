## ADDED Requirements

### Requirement: Allow web_search tools and reject unsupported built-ins
The service MUST accept Responses requests that include tools with type `web_search` or `web_search_preview`. The service MUST normalize `web_search_preview` to `web_search` before forwarding upstream. The service MUST reject other built-in tool types (file_search, code_interpreter, computer_use, computer_use_preview, image_generation) with an OpenAI invalid_request_error.

#### Scenario: web_search_preview tool accepted
- **WHEN** the client sends `tools=[{"type":"web_search_preview"}]`
- **THEN** the service accepts the request and forwards the tool as `web_search`

#### Scenario: unsupported built-in tool rejected
- **WHEN** the client sends `tools=[{"type":"code_interpreter"}]`
- **THEN** the service returns a 4xx response with an OpenAI invalid_request_error indicating the unsupported tool type
