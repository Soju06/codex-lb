### MODIFIED Requirement: Validate request structure and unsupported fields
The service MUST accept `input` as either a string or an array of input items. When `input` is a string, the service MUST normalize it into a single user input item with `input_text` content before forwarding upstream. The service MUST continue to reject requests that include both `conversation` and `previous_response_id`.

#### Scenario: conversation and previous_response_id conflict
- **WHEN** the client provides both `conversation` and `previous_response_id`
- **THEN** the service rejects the request with an OpenAI error envelope identifying the conflicting field

#### Scenario: previous_response_id provided
- **WHEN** the client provides `previous_response_id` without `conversation`
- **THEN** the service accepts the request and forwards `previous_response_id` upstream unchanged

### MODIFIED Requirement: WebSocket Responses proxy preserves request shape
When proxying websocket `response.create` requests, the service MUST preserve supported incremental request fields required by native Codex clients. The service MUST forward `previous_response_id` unchanged when present and MUST continue to omit only HTTP-only transport fields such as `stream` and `background` from the upstream websocket payload.

#### Scenario: websocket response.create includes previous_response_id
- **WHEN** a websocket `response.create` payload includes a non-empty `previous_response_id`
- **THEN** the upstream websocket payload includes the same `previous_response_id`
