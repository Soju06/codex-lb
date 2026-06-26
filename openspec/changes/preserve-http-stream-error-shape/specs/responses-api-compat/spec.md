## ADDED Requirements

### Requirement: Preserve raw backend stream error frames when contract mode is disabled

The proxy MUST preserve raw backend stream error frames when contract mode is
disabled. When the proxy serves `POST /backend-api/codex/responses` with
`enforce_openai_sdk_contract=False`, it MUST forward upstream HTTP SSE frames
with `type: "error"` unchanged on the stream. In this mode, no
`response.failed` synthesis is allowed before `yield` for those upstream frames.

#### Scenario: Raw backend error passthrough

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling sets `enforce_openai_sdk_contract=False`
- **WHEN** the proxy forwards that upstream event in the public stream
- **THEN** the downstream event MUST remain an `error` event
- **AND** `sequence_number`, `error_type`, and message fields from upstream must remain unchanged
- **AND** the event SHOULD NOT be rewritten into `response.failed` in the same stream step

### Requirement: Keep default contract shaping enabled unless explicitly disabled

The proxy MUST keep default contract shaping enabled unless explicitly
disabled. For backward-compatible behavior, when
`enforce_openai_sdk_contract` is omitted or `True`, current error-shaping
behavior MUST remain in place and convert error-type SSE frames as defined by
existing `responses-api-compat` contracts.

#### Scenario: Default public contract still emits response.failed

- **GIVEN** a streaming HTTP upstream response emits:
  `data: {"type":"error","sequence_number":"error","error_type":"server_error",...}`
- **AND** request handling omits `enforce_openai_sdk_contract` or sets it to `True`
- **WHEN** the proxy forwards that upstream event
- **THEN** the downstream event MUST be normalized to `response.failed`
