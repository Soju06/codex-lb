## ADDED Requirements

### Requirement: Downstream websocket ingress accepts large response.create messages
The server MUST accept client-to-proxy websocket messages on the Responses websocket routes (`/backend-api/codex/responses`, `/v1/responses`) up to a configurable ingress budget before closing the connection at the protocol layer. The default budget MUST be 128 MiB, matching the HTTP responses-path decompressed body cap. The budget MUST be configurable via the `--ws-max-size` CLI flag and the `UVICORN_WS_MAX_SIZE` environment variable, with the CLI flag taking precedence. The server MUST continue to negotiate `permessage-deflate` on the client-facing websocket, and the ingress budget MUST apply to the decompressed message size.

#### Scenario: Oversized response.create reaches the application-level guard
- **WHEN** a client sends a single websocket text message larger than 16 MiB but within the configured ingress budget
- **THEN** the server delivers the message to the application layer instead of closing the connection with `1009 message too big`
- **AND** the application-level oversized-`response.create` handling (historical slimming, then local rejection) applies

#### Scenario: Operator overrides the ingress budget
- **WHEN** the operator starts the server with `--ws-max-size <bytes>` or sets `UVICORN_WS_MAX_SIZE=<bytes>`
- **THEN** the websocket ingress message budget uses the configured value
- **AND** an invalid (non-positive or non-integer) value fails startup with a clear error

### Requirement: Oversized response.create payloads are slimmed or rejected fail-fast before upstream send
When the service prepares a Responses `response.create` request for the upstream websocket, it MUST measure the serialized outbound request size before sending it upstream. If the payload exceeds the upstream websocket budget, the service MUST first attempt to slim only the historical portion of `input` that precedes the most recent user turn: historical inline images MUST be replaced with textual omission notices, and oversized historical tool outputs MUST be replaced with textual omission notices that preserve the item in sequence. If the request still exceeds budget after slimming, the service MUST fail locally with status `400` — not `413` — carrying `error.code = "payload_too_large"`, `error.type = "invalid_request_error"`, and `error.param = "input"`, because the official Codex client treats `400` as a non-retryable invalid-request error surfaced immediately while `413` triggers five full-payload retries followed by a sticky session-wide websocket-to-HTTP transport downgrade.

#### Scenario: Historical inline artifacts are slimmed and the latest user turn is preserved
- **WHEN** a Responses request exceeds the upstream websocket budget because historical inline images or historical oversized tool outputs dominate the serialized `input`
- **AND** replacing those historical artifacts with omission notices reduces the serialized request below budget
- **THEN** the service forwards the slimmed `response.create` upstream
- **AND** it preserves the most recent user turn unchanged

#### Scenario: HTTP Responses route fails locally with 400 when the payload still exceeds budget
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` request still exceeds the upstream websocket budget after historical slimming
- **THEN** the service returns HTTP `400`
- **AND** the error envelope code is `payload_too_large`
- **AND** the error envelope type is `invalid_request_error`
- **AND** the error envelope param is `input`
- **AND** the service MUST NOT allocate or reuse an upstream websocket bridge session for that request

#### Scenario: Websocket Responses route fails locally with a status-400 error event when the payload still exceeds budget
- **WHEN** a websocket `/v1/responses` or `/backend-api/codex/responses` request still exceeds the upstream websocket budget after historical slimming
- **THEN** the service emits a websocket error event with `"type": "error"` and `"status": 400`
- **AND** the error envelope code is `payload_too_large`
- **AND** the error envelope type is `invalid_request_error`
- **AND** the error envelope param is `input`
- **AND** the service MUST NOT connect the upstream websocket for that request
