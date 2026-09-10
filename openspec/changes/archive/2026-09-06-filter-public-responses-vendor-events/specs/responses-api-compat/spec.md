# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: Public /v1 responses SSE stream emits only OpenAI Responses contract events

When serving streaming `POST /v1/responses`, the service MUST forward a
string-valued event type only when it is exactly `error` or begins with
`response.`. Other string-valued event types MUST be dropped before they reach
the public stream. OpenAI-shaped backend requests with public contract
enforcement enabled MUST follow the same filtering rule. Native Codex requests
with public contract enforcement disabled MUST retain upstream vendor events.

#### Scenario: Codex-internal rate-limit event is dropped before response.created

- **WHEN** upstream emits `codex.rate_limits` before `response.created` for a streaming `/v1/responses` request
- **THEN** the public stream MUST NOT contain `codex.rate_limits`
- **AND** its first event MUST be `response.created`

#### Scenario: Timing diagnostics are filtered without losing text or completion

- **WHEN** upstream emits `responsesapi.websocket_timing` before, between, or after standard response events
- **THEN** a public-contract stream MUST NOT contain that diagnostic
- **AND** standard text deltas and completion events MUST remain in order

#### Scenario: OpenAI-shaped backend request filters vendor events

- **WHEN** an OpenAI-shaped `/backend-api/codex/responses` request enables public contract enforcement
- **THEN** its response stream MUST apply the public event-family filtering rule

#### Scenario: Codex-internal events on the Codex CLI route are preserved

- **WHEN** a native `/backend-api/codex/responses` request disables public contract enforcement
- **THEN** the response stream MUST retain `codex.rate_limits` and `responsesapi.websocket_timing` in upstream order
