## ADDED Requirements

### Requirement: Parameterless invalid previous-response errors use continuity recovery

When an upstream Responses WebSocket rejects an anchored request with `type = "invalid_request_error"`, no `code` or `param`, and the normalized message ``Invalid `previous_response_id``` with or without one trailing period, the service MUST classify the frame as a previous-response continuity miss. It MUST apply the same replay, masking, ownership, and account-health rules as the canonical `previous_response_not_found` error and MUST NOT relay the raw invalid-request frame downstream. A different named parameter or any other trailing punctuation MUST NOT match this error shape.

#### Scenario: Codex-native delta continuation receives the canonical recovery signal

- **GIVEN** a Codex-native `/backend-api/codex/responses` request carries `previous_response_id` and delta-only tool output that cannot be replayed safely without its anchor
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error before `response.created`
- **THEN** the downstream client receives a sanitized error with `code = "previous_response_not_found"`
- **AND** the raw upstream envelope and previous response id are not exposed

#### Scenario: Self-contained full resend is replayed without the rejected anchor

- **GIVEN** an anchored direct WebSocket request retains a self-contained full-resend body that is safe to replay without `previous_response_id`
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error before `response.created`
- **THEN** the service reconnects and replays the retained body without `previous_response_id`
- **AND** the raw upstream error is not sent downstream

#### Scenario: Public WebSocket retains generic continuity masking

- **GIVEN** a public `/v1/responses` WebSocket request carries `previous_response_id` but cannot be replayed safely without its anchor
- **WHEN** upstream returns the parameterless ``Invalid `previous_response_id`.`` error
- **THEN** the downstream client receives the existing sanitized `stream_incomplete` continuity failure
- **AND** neither `previous_response_not_found` nor the raw upstream envelope is exposed

#### Scenario: Unrelated invalid requests retain their original classification

- **WHEN** upstream returns `invalid_request_error` with a different message or names a parameter other than `previous_response_id`
- **THEN** the service MUST NOT classify that error as a previous-response continuity miss
