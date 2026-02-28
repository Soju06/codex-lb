## MODIFIED Requirements

### Requirement: SDK-backed Anthropic Messages API compatibility
The service MUST accept `POST /claude/v1/messages` and execute generation via
Codex proxy transport. The route MUST expose Anthropic-style response envelopes
for streaming and non-streaming calls while translating request/response
structures between Anthropic Messages and OpenAI-compatible proxy payloads.

`POST /claude-sdk/v1/messages` MUST remain available for SDK transport behavior.

#### Scenario: Claude-compatible request is handled by Codex proxy transport
- **WHEN** a client sends a valid Anthropic Messages payload to `/claude/v1/messages`
- **THEN** the service translates the request to an OpenAI-compatible responses payload
- **AND** executes the request via proxy service transport
- **AND** returns an Anthropic-compatible response envelope

#### Scenario: Codex proxy transport error is surfaced
- **WHEN** proxy execution returns an upstream error for `/claude/v1/messages`
- **THEN** the service returns an Anthropic-style error envelope with mapped HTTP status

### Requirement: Support streaming and non-streaming Messages responses
The service MUST support both `stream=true` (SSE) and non-streaming Messages
requests on `/claude/v1/messages`.

#### Scenario: Streaming response
- **WHEN** `stream=true`
- **THEN** the service responds with `text/event-stream`
- **AND** emits Anthropic message SSE events derived from proxy completion output

#### Scenario: Non-streaming response
- **WHEN** `stream` is `false` or omitted
- **THEN** the service returns an Anthropic-compatible JSON message payload

### Requirement: Claude Desktop startup compatibility endpoints
The service MUST expose minimal startup endpoints required by Claude Desktop
custom deployment mode so the client can bootstrap without hard failures.

#### Scenario: Desktop bootstrap request
- **WHEN** a client calls `GET /api/bootstrap`
- **THEN** the service returns HTTP 200 with a JSON object that includes an `account` object

#### Scenario: Desktop feature fetch
- **WHEN** a client calls `GET /api/desktop/features`
- **THEN** the service returns HTTP 200 with a JSON object containing a `features` map

#### Scenario: Desktop event logging batch
- **WHEN** a client calls `POST /api/event_logging/batch`
- **THEN** the service returns HTTP 200 acknowledging the batch
