## ADDED Requirements

### Requirement: Model-capacity messages are retryable transient failures

When upstream returns a temporary model-capacity failure whose message says that the selected model is at capacity, the proxy MUST treat the failure as retryable transient even if the upstream error code or HTTP status would otherwise look non-retryable.

#### Scenario: Selected model capacity with invalid request code is retryable

- **WHEN** upstream returns an error envelope with `error.message = "Selected model is at capacity. Please try a different model."`
- **AND** the normalized error code is `invalid_request_error`
- **AND** the HTTP status is `400`
- **THEN** `classify_upstream_failure` returns `failure_class = "retryable_transient"`
- **AND** pre-visible streaming/websocket paths are eligible to retry or fail over instead of surfacing a terminal client error.

#### Scenario: Serialized selected-model capacity event can retry before visibility

- **WHEN** a streaming Responses request receives a first upstream `response.failed` or `error` event whose message says the selected model is at capacity
- **AND** the event does not include an upstream response id
- **AND** no downstream-visible output has been emitted
- **THEN** the stream retry layer MUST treat the event as a retryable transient failure inside the existing bounded same-account retry budget
- **AND** the retry layer MUST preserve the existing no-replay behavior once downstream-visible output exists.

#### Scenario: Post-connect body-read disconnect is not replayed as capacity retry

- **WHEN** a streaming Responses request fails while reading the upstream stream body after the upstream request has been dispatched
- **AND** the failure is an `aiohttp` client error, timeout, EOF, or other transport/body-read close without typed pre-dispatch provenance
- **THEN** the proxy MUST surface the stream failure to the downstream client
- **AND** the proxy MUST NOT transparently re-POST the request as a model-capacity retry.

#### Scenario: Quota and rate-limit codes retain their stronger classification

- **WHEN** upstream returns a quota or rate-limit error code
- **THEN** the proxy MUST keep classifying it as quota or rate-limit before applying message-based model-capacity detection.
