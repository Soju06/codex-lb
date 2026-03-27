### MODIFIED Requirement: Support Responses input types and conversation constraints
The service MUST accept `input` as either a string or an array of input items. When `input` is a string, the service MUST normalize it into a single user input item with `input_text` content before forwarding upstream. The service MUST accept `previous_response_id` when `conversation` is absent, MUST prefer live upstream continuity when available, and otherwise MUST resolve that response id from caller-scoped persisted continuity state before forwarding upstream. The service MUST continue to reject requests that include both `conversation` and `previous_response_id`.

#### Scenario: conversation and previous_response_id conflict
- **WHEN** the client provides both `conversation` and `previous_response_id`
- **THEN** the service rejects the request with an OpenAI error envelope identifying the conflicting field

#### Scenario: previous_response_id provided
- **WHEN** the client provides `previous_response_id` without `conversation`
- **THEN** the service accepts the request
- **AND** it either preserves live upstream continuity or rebuilds the request locally from persisted continuity state before forwarding upstream

#### Scenario: previous_response_id is outside caller scope
- **WHEN** the client provides `previous_response_id`
- **AND** the stored continuity belongs to another API key scope or does not exist
- **THEN** the service returns `invalid_request_error` on `previous_response_id`

### MODIFIED Requirement: HTTP Responses routes preserve upstream websocket session continuity
When serving HTTP `/v1/responses` or HTTP `/backend-api/codex/responses`, the service MUST preserve upstream Responses websocket session continuity on a stable per-session bridge key when that live session is available. If that live session is unavailable and caller-scoped persisted continuity exists for `previous_response_id`, the service MUST rebuild the request locally and complete it through a fresh upstream request without opening a replacement bridge session that forwards the old `previous_response_id` upstream unchanged.

#### Scenario: HTTP bridge loss falls back to persisted replay
- **WHEN** a client sends HTTP `/v1/responses` or `/backend-api/codex/responses` with `previous_response_id`
- **AND** there is no matching live bridged upstream session
- **AND** caller-scoped persisted continuity exists
- **THEN** the service rebuilds the request locally from persisted continuity state and completes it successfully

### ADDED Requirement: Websocket Responses retry one request after early upstream disconnect
When an upstream Responses websocket disconnects before `response.created`, the service MUST retry at most one pending request on another eligible account when exactly one request is in flight and that request has not yet been acknowledged upstream.

#### Scenario: upstream disconnects before response.created
- **WHEN** exactly one websocket `response.create` request is pending
- **AND** the upstream disconnects before emitting `response.created`
- **THEN** the service retries that request once on another eligible account
