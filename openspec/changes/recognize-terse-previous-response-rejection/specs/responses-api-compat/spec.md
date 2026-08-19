## MODIFIED Requirements

### Requirement: Public Responses errors mask previous-response misses

Public Responses endpoints MUST NOT return an OpenAI-shaped `previous_response_not_found` error to clients. If a lower layer still raises or collects that error, the API layer MUST rewrite it to a retryable `stream_incomplete` continuity failure and remove the missing response id from the public payload. An upstream rejection of the anchor MUST be recognized from its wording as well as its canonical code: an `invalid_request_error` whose message names `previous_response_id` as invalid or not found is a previous-response miss even when the error carries no `param`. An error whose `param` names a different field MUST NOT be treated as a previous-response miss.

#### Scenario: API layer receives an upstream previous-response miss

- **WHEN** a public `/responses`, `/v1/responses`, `/responses/compact`, or `/v1/responses/compact` handler receives an error with `code=previous_response_not_found`
- **OR** it receives `code=invalid_request_error` with `param=previous_response_id` and a message saying the previous response was not found
- **THEN** the response status is retryable
- **AND** the public error code is `stream_incomplete`
- **AND** the missing `previous_response_id` is not exposed in the response body

#### Scenario: Upstream rejects the anchor without a param or "not found" wording

- **WHEN** a public Responses handler receives `code=invalid_request_error` with no `param` and a message of ``Invalid `previous_response_id`.``
- **THEN** it is treated as a previous-response miss
- **AND** the public error code is `stream_incomplete` rather than the raw upstream `invalid_request_error`

#### Scenario: An error naming a different param is not a previous-response miss

- **WHEN** a public Responses handler receives `code=invalid_request_error` whose `param` names a field other than `previous_response_id`
- **THEN** it is not treated as a previous-response miss, whatever its message says
