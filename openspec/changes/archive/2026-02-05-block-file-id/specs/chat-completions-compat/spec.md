## MODIFIED Requirements

### Requirement: Reject file_id in Chat Completions
The service MUST reject chat `file` content parts that include `file_id` and return a 4xx OpenAI invalid_request_error with message "Invalid request payload".

#### Scenario: file_id rejected in chat file part
- **WHEN** a user message includes `{ "type": "file", "file": {"file_id":"file_123"} }`
- **THEN** the service returns a 4xx OpenAI invalid_request_error with message "Invalid request payload" and param `messages`
