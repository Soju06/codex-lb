# responses-api-compat delta

## MODIFIED Requirements

### Requirement: Oversized response.create payloads are slimmed or rejected fail-fast before upstream send

When the service prepares a Responses `response.create` request for the upstream websocket, it MUST measure the serialized outbound request size before sending it upstream. If the payload exceeds the upstream websocket budget, the service MUST first attempt to slim only the historical portion of `input` that precedes the most recent user turn: historical inline images MUST be replaced with textual omission notices, and oversized historical tool outputs MUST be replaced with textual omission notices that preserve the item in sequence. Historical slimming MUST cover tool-call output items of every supported type — `function_call_output`, `custom_tool_call_output`, and `apply_patch_call_output` — including inline images nested inside list- or mapping-valued `output` content parts, which MUST be replaced with the image omission notice while non-image parts, item order, `call_id`, and `status` fields are preserved. If the request still exceeds budget after slimming, the service MUST fail locally with status `400` — not `413` — carrying `error.code = "payload_too_large"`, `error.type = "invalid_request_error"`, and `error.param = "input"`, because the official Codex client treats `400` as a non-retryable invalid-request error surfaced immediately while `413` triggers five full-payload retries followed by a sticky session-wide websocket-to-HTTP transport downgrade.

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

#### Scenario: Inline images nested in historical tool-call outputs are slimmed
- **GIVEN** an oversized `response.create` whose historical `input` contains a
  `custom_tool_call_output` (or `function_call_output` /
  `apply_patch_call_output`) whose `output` is a list of content parts
  including `data:image/` inline images
- **WHEN** the size guard triggers historical slimming
- **THEN** each nested inline image part is replaced with the image omission
  notice part
- **AND** non-image parts, item order, `call_id`, and `status` are preserved
- **AND** the slimmed request is forwarded upstream when it fits the budget

#### Scenario: Oversized string outputs are slimmed for all tool-call output types
- **GIVEN** a historical `custom_tool_call_output` or `apply_patch_call_output`
  whose string `output` exceeds the oversized-tool-output threshold
- **WHEN** the size guard triggers historical slimming
- **THEN** the string output is replaced with the tool-output omission notice,
  matching the existing `function_call_output` behavior
