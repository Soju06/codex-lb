## ADDED Requirements

### Requirement: Model-capacity messages are retryable transient failures

When upstream returns a temporary model-capacity failure whose message says that the selected model is at capacity, the proxy MUST treat the failure as retryable transient even if the upstream error code or HTTP status would otherwise look non-retryable.

#### Scenario: Selected model capacity with invalid request code is retryable

- **WHEN** upstream returns an error envelope with `error.message = "Selected model is at capacity. Please try a different model."`
- **AND** the normalized error code is `invalid_request_error`
- **AND** the HTTP status is `400`
- **THEN** `classify_upstream_failure` returns `failure_class = "retryable_transient"`
- **AND** pre-visible streaming/websocket paths are eligible to retry or fail over instead of surfacing a terminal client error.

#### Scenario: Serialized selected-model capacity event surfaces without replay

- **WHEN** a streaming Responses request receives a first upstream `response.failed` or `error` event whose message says the selected model is at capacity
- **AND** no downstream-visible output has been emitted
- **THEN** the proxy MUST surface that terminal event without transparently re-POSTing the request
- **AND** the absence of an upstream response id MUST NOT by itself prove the POST was safe to replay.

#### Scenario: Post-connect body-read disconnect is not replayed as capacity retry

- **WHEN** a streaming Responses request fails while reading the upstream stream body after the upstream request has been dispatched
- **AND** the failure is an `aiohttp` client error, timeout, EOF, or other transport/body-read close without typed pre-dispatch provenance
- **THEN** the proxy MUST surface the stream failure to the downstream client
- **AND** the proxy MUST NOT transparently re-POST the request as a model-capacity retry.

#### Scenario: Websocket connect failure retries before request dispatch

- **WHEN** an upstream websocket handshake raises a typed connector failure or connect timeout before the `response.create` frame is sent
- **THEN** the proxy MUST preserve typed pre-dispatch provenance and MAY retry or fail over before any downstream-visible output
- **AND** a websocket transport selection MUST NOT turn that failure into a terminal serialized SSE event.

#### Scenario: Direct HTTP TLS verification failure is not retried

- **WHEN** a direct HTTP stream raises a certificate or TLS connector failure before request dispatch
- **THEN** the proxy MUST surface the TLS failure without transparently retrying or failing over
- **AND** pre-dispatch provenance MUST NOT classify the non-transient TLS failure as retryable.

#### Scenario: Quota and rate-limit codes retain their stronger classification

- **WHEN** upstream returns a quota or rate-limit error code
- **THEN** the proxy MUST keep classifying it as quota or rate-limit before applying message-based model-capacity detection.

#### Scenario: Post-refresh transient exhaustion preserves every health signal

- **WHEN** one or more accounts each exhaust multiple same-account post-refresh transient retries before the request succeeds or terminates
- **THEN** the proxy MUST settle API-key usage before recording any deferred account-health failure
- **AND** each exhausted account MUST receive exactly one classified health failure plus one additional failure for every remaining exhausted retry
- **AND** selecting or exhausting a later account MUST NOT replace, lose, or duplicate an earlier account's deferred failures.
