## ADDED Requirements

### Requirement: Reject input_audio in Chat Completions
The service MUST reject chat user content parts with type `input_audio` and return a 4xx OpenAI invalid_request_error.

#### Scenario: input_audio rejected
- **WHEN** a user message includes `{ "type": "input_audio", "input_audio": {"data":"...","format":"wav"} }`
- **THEN** the service returns a 4xx OpenAI invalid_request_error indicating audio input is unsupported

### Requirement: Allow file_id mapping in Chat Completions
The service MUST map chat `file` content parts with `file_id` to Responses `input_file` with `file_id` and MUST not reject them locally.

#### Scenario: file_id mapped to input_file
- **WHEN** a user message includes `{ "type": "file", "file": {"file_id":"file_123"} }`
- **THEN** the mapped Responses request includes `{ "type": "input_file", "file_id": "file_123" }`
