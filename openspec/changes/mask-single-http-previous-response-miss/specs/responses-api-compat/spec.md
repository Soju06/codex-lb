## ADDED Requirements

### Requirement: Single HTTP bridge previous-response misses recover or fail closed
When an HTTP bridge session receives an anonymous upstream `previous_response_not_found` error for a single pending follow-up request, the service MUST treat the error as an internal continuity-loss signal. It MUST either recover through the existing previous-response rebind path or rewrite the error to a retryable continuity failure instead of forwarding the raw upstream invalid-request error.

#### Scenario: single pending HTTP bridge follow-up loses previous-response continuity
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` bridge session has exactly one pending request with `previous_response_id`
- **AND** upstream emits `previous_response_not_found` without a `response.id`
- **THEN** the service attempts the existing previous-response recovery path
- **AND** if recovery is unavailable, it emits a retryable continuity failure for that request
- **AND** the downstream error code is not `previous_response_not_found`

### Requirement: WebSocket full-resend previous-response misses retry without stale anchor
When a direct WebSocket `response.create` request includes both `previous_response_id` and a full resend payload, the service MUST retain a safe replay body without `previous_response_id`. If upstream rejects the anchor with `previous_response_not_found` before `response.created`, the service MUST reconnect and replay the retained full payload as a fresh turn instead of forwarding the raw upstream invalid-request error.

#### Scenario: full-resend WebSocket follow-up loses just-completed anchor
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload also carries enough input to be treated as a full resend
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service reconnects the upstream WebSocket
- **AND** it replays the same request without `previous_response_id`
- **AND** the downstream client receives the recovered response events, not the raw `previous_response_not_found` error

### Requirement: Public Responses errors mask previous-response misses
Public Responses endpoints MUST NOT return an OpenAI-shaped `previous_response_not_found` error to clients. If a lower layer still raises or collects that error, the API layer MUST rewrite it to a retryable `stream_incomplete` continuity failure and remove the missing response id from the public payload.

#### Scenario: API layer receives an upstream previous-response miss
- **WHEN** a public `/responses`, `/v1/responses`, `/responses/compact`, or `/v1/responses/compact` handler receives an error with `code=previous_response_not_found`
- **OR** it receives `code=invalid_request_error` with `param=previous_response_id` and a message saying the previous response was not found
- **THEN** the response status is retryable
- **AND** the public error code is `stream_incomplete`
- **AND** the missing `previous_response_id` is not exposed in the response body
