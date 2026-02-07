## MODIFIED Requirements

### Requirement: Reject input_file file_id in Responses
The service MUST reject `input_file.file_id` in Responses input items and return a 4xx OpenAI invalid_request_error with message "Invalid request payload".

#### Scenario: input_file file_id rejected
- **WHEN** a request includes an input item with `{"type":"input_file","file_id":"file_123"}`
- **THEN** the service returns a 4xx OpenAI invalid_request_error with message "Invalid request payload" and param `input`
