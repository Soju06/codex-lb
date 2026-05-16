## ADDED Requirements

### Requirement: Public /v1 responses SSE stream emits only OpenAI Responses contract events
When serving streaming `POST /v1/responses`, the service MUST emit only event types defined by the OpenAI Responses SSE contract (the `response.*` and `error` families) on the public stream. The service MUST drop any vendor-internal event types — specifically, any event whose `type` begins with `codex.` (for example `codex.rate_limits`) — before they reach the public stream. The `/backend-api/codex/*` routes are NOT subject to this requirement and MUST continue forwarding these events unchanged.

#### Scenario: Codex-internal rate-limit event is dropped before response.created
- **WHEN** the upstream Codex backend emits `codex.rate_limits` before `response.created` for a streaming `/v1/responses` request
- **THEN** the public stream MUST NOT contain the `codex.rate_limits` event
- **AND** the first event the public stream emits MUST be `response.created`

#### Scenario: Codex-internal events on the Codex CLI route are preserved
- **WHEN** the upstream emits `codex.rate_limits` for a `POST /backend-api/codex/responses` request
- **THEN** the response stream forwards the `codex.rate_limits` event to the Codex CLI client unchanged

### Requirement: Streamed /v1 responses terminal output is backfilled from item events
When serving streaming `POST /v1/responses`, if the upstream's terminal `response.completed` or `response.incomplete` event carries `output` as missing or as an empty list, the service MUST reconstruct `output` from the `response.output_item.done` events emitted earlier in the same stream before yielding the terminal SSE event. The reconstructed `output` MUST preserve the `output_index` ordering and the raw item payloads. When the terminal `response.completed` / `response.incomplete` already carries a non-empty `output`, the service MUST forward it unchanged.

#### Scenario: Terminal response.completed with empty output is backfilled from streamed items
- **GIVEN** the upstream emits `response.output_item.done` events with valid message or function-call items
- **WHEN** the upstream's terminal `response.completed` event carries `output: []`
- **THEN** the public stream's terminal `response.completed` event MUST carry the reconstructed `output` array, populated from the streamed `output_item.done` items in `output_index` order
- **AND** an OpenAI Python SDK consumer calling `stream.get_final_response().output` MUST receive the same populated list

#### Scenario: Terminal response.completed already carries output
- **WHEN** the upstream's terminal `response.completed` event already includes a non-empty `output` array
- **THEN** the public stream's terminal event MUST carry that `output` array unchanged

### Requirement: Public /v1 responses SSE stream starts with response.created
When serving streaming `POST /v1/responses`, the first OpenAI-contract event the public stream emits MUST be `response.created`. When the upstream's first standard `response.*` event is not `response.created` (for example when the Codex backend jumps directly to `response.failed` on upstream rejection mid-stream), the service MUST synthesize a `response.created` SSE event from the source event's `response` envelope and emit it before forwarding the source event, so that consumers using the OpenAI Python SDK's `responses.stream(...)` parser do not raise `RuntimeError`.

#### Scenario: Upstream error stream that skips response.created is repaired
- **WHEN** the upstream's first standard event is `response.failed` (no preceding `response.created`)
- **THEN** the public stream MUST emit a synthesized `response.created` event derived from the failed event's `response` envelope before forwarding the `response.failed` event
- **AND** an OpenAI Python SDK consumer iterating the stream MUST NOT raise `RuntimeError` from the parser's initial-response check

#### Scenario: Normal stream is not double-emitted
- **WHEN** the upstream's first standard event is already `response.created`
- **THEN** the public stream MUST emit exactly one `response.created` event (no synthesized duplicate)
