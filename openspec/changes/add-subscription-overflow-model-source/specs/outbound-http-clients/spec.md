## ADDED Requirements

### Requirement: OpenAI-compatible model-source transport is bounded and isolated

Every request the proxy forwards to an OpenAI-compatible model source MUST use a dedicated outbound connector pool that is separate from the ChatGPT upstream connector. The pool MUST be sized by `http_connector_limit` / `http_connector_limit_per_host`, MUST tunnel through the same SOCKS proxy configuration as the ChatGPT pool, and MUST be built and retired with the shared client generation: rotation defers closing the retired pool until every in-flight source exchange has released its lease. A source that accepts connections and stalls MUST NOT consume ChatGPT connector slots.

Source exchanges MUST bound their exposure per phase. Connection establishment MUST be bounded by 10 seconds (`connect` and `sock_connect`); a connect timeout keeps the existing `502 model_source_unreachable` verdict and records the `connect` phase. For streaming requests the wait for response headers MUST be bounded by 20 seconds and the wait for the first body chunk after the headers by 30 seconds; either expiry MUST fail the request with HTTP `504` and error code `model_source_timeout` before any byte of a `200` stream reaches the client, and MUST release the source connection. Mid-stream silence MUST be bounded by the smaller of `stream_idle_timeout_seconds` and 300 seconds — a source never inherits the subscription idle window — and an expiry MUST surface to the stream owner as `504 model_source_idle_timeout`. A `2xx` stream that ends before its first chunk MUST fail with `502 invalid_upstream_response`. Non-stream forwards MUST bound only connection establishment and the source's total budget (`timeout_seconds`, default 600 s), so a long generation that sends nothing until its final body is not cut by a header or first-frame deadline. The stream deadlines MUST be armed through the owner scheduler seam so simulated time can expire them.

Source `4xx`/`5xx` answers MUST pass through honestly: the source status code, the source error envelope with the proxy's source credential redacted, and the source `Retry-After` value MUST be preserved for the caller, and the proxy MUST NOT synthesize a `Retry-After`. For Responses dispatch a source `401` or `403` MUST instead be answered with `502 model_source_credentials_error` and a fixed generic message; the source body MUST NOT be read, forwarded, or logged, and the server-side record MUST carry only the source id and the status code. Chat-completions, transcription, and embeddings source routes keep passing the source's `401`/`403` envelope through.

The stream body MUST yield the first chunk the open already read before reading further, so the first-frame deadline is the only wait between the headers and the first byte the client receives.

#### Scenario: source accepts the TCP connection but never sends headers

- **WHEN** a streaming Responses request is forwarded to a source that accepts the connection and sends nothing for 20 seconds
- **THEN** the request fails with HTTP `504` and `error.code` `model_source_timeout`
- **AND** the source connection is released
- **AND** no byte of a `200` stream was sent to the client

#### Scenario: source sends headers and then no frame

- **WHEN** a streaming source answers `200 text/event-stream` and sends no body byte for 30 seconds
- **THEN** the request fails with HTTP `504` and `error.code` `model_source_timeout`
- **AND** the source connection is closed rather than returned to the pool

#### Scenario: stream idle cap never inherits the subscription window

- **WHEN** `stream_idle_timeout_seconds` is 7200 and a source stream goes silent after `response.created`
- **THEN** the read timeout armed on the source socket is 300 seconds
- **AND** its expiry surfaces as `model_source_idle_timeout`, never after 7200 seconds

#### Scenario: non-stream generation outlives the stream deadlines

- **WHEN** a non-stream Responses request is forwarded to a source whose complete body arrives after 20 minutes within the source's total budget
- **THEN** the response is delivered
- **AND** neither the header nor the first-frame deadline is armed

#### Scenario: stalled sources do not consume the ChatGPT connector

- **WHEN** fifty streaming source opens stall waiting for headers
- **THEN** the ChatGPT connector has no acquired connections attributable to them
- **AND** the model-source connector holds those fifty connections until the header deadline releases them

#### Scenario: source 429 passes through with Retry-After

- **WHEN** a source answers `429` with `Retry-After: 7` and an error envelope
- **THEN** the forwarding error carries status `429`, the source envelope, and `retry_after` `"7"`

#### Scenario: source 401 on Responses dispatch is recoded without leaking the key

- **WHEN** a source answers a Responses request with `401` and a body that embeds a masked copy of the proxy's source credential
- **THEN** the request fails with HTTP `502` and `error.code` `model_source_credentials_error`
- **AND** no fragment of the source body appears in the client envelope or in any log record
- **AND** a `WARNING` record names the source id and the status `401` only

#### Scenario: chat completions keep the source 401 envelope

- **WHEN** a source answers a streaming chat-completions request with `401 invalid_api_key`
- **THEN** the client receives HTTP `401` with `error.code` `invalid_api_key`
