## ADDED Requirements

### Requirement: Chat Completions continues rejecting unsupported built-in tools

The service MUST continue rejecting unsupported built-in tool definitions on `/v1/chat/completions`, even though Responses-family endpoints allow them. Unsupported built-in chat tool types include `file_search`, `code_interpreter`, `computer_use`, `computer_use_preview`, and `image_generation`.

#### Scenario: Chat request with image_generation is rejected

- **WHEN** a client sends `/v1/chat/completions` with `tools=[{"type":"image_generation"}]`
- **THEN** the service returns a 4xx OpenAI invalid_request_error
- **AND** the request is not forwarded upstream
