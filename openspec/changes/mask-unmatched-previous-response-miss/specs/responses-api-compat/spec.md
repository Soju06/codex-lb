## MODIFIED Requirements

### Requirement: Public Responses errors mask previous-response misses
Public Responses endpoints MUST NOT return an OpenAI-shaped previous_response_not_found error to clients. If a lower layer still raises or collects that error, the API layer MUST rewrite it to a retryable stream_incomplete continuity failure and remove the missing response id from the public payload.

#### Scenario: API layer receives an upstream previous-response miss
- **WHEN** a public /responses, /v1/responses, /responses/compact, or /v1/responses/compact handler receives an error with code=previous_response_not_found
- **OR** it receives code=invalid_request_error with param=previous_response_id and a message saying the previous response was not found
- **THEN** the response status is retryable
- **AND** the public error code is stream_incomplete
- **AND** the missing previous_response_id is not exposed in the response body

#### Scenario: unmatched websocket previous-response miss is masked
- **WHEN** an upstream Responses websocket emits previous_response_not_found before the proxy can match it to a response id
- **AND** the event can be associated with exactly one pending client request or must otherwise be treated as a continuity failure
- **THEN** the proxy MUST NOT forward the raw upstream previous_response_not_found event
- **AND** the downstream error code is stream_incomplete when a pending request can be safely completed
- **AND** ambiguous events that cannot be safely associated with a request are suppressed or converted into a reconnect without exposing the raw upstream error
