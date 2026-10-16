# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: WebSocket full-resend previous-response misses retry without stale anchor
When a direct WebSocket `response.create` request includes both `previous_response_id` and a self-contained full resend payload, the service MUST retain a safe replay body without `previous_response_id`. If upstream rejects the anchor with `previous_response_not_found` before `response.created`, the service MUST reconnect and replay the retained full payload as a fresh turn instead of forwarding the raw upstream invalid-request error. A payload that only carries incremental tool outputs for tool calls that are not also present in the same request is not self-contained and MUST NOT be replayed as a fresh turn without `previous_response_id`. A payload whose `previous_response_id` names an empty-output prewarm completed on the same connection is self-contained only if its input begins with that prewarm's input; otherwise it MUST NOT be replayed as a fresh turn without `previous_response_id`, because the prewarm alone carried that context.

#### Scenario: full-resend WebSocket follow-up loses just-completed anchor
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload also carries enough input to be treated as a full resend
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service reconnects the upstream WebSocket
- **AND** it replays the same request without `previous_response_id`
- **AND** the downstream client receives the recovered response events, not the raw `previous_response_not_found` error

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** the request payload carries `function_call_output`, `custom_tool_call_output`, or `apply_patch_call_output` items without their matching tool-call items in the same payload
- **AND** upstream emits `previous_response_not_found` before assigning a response id
- **THEN** the service MUST NOT replay that payload as a fresh turn without `previous_response_id`
- **AND** the downstream client receives a retryable continuity failure rather than a fabricated fresh turn

#### Scenario: delta chained to an empty prewarm is not replayed as a fresh turn
- **GIVEN** a direct WebSocket handshake carries `x-codex-turn-metadata` with `request_kind: "prewarm"`
- **AND** a `generate: false` request on that connection completed with zero output tokens
- **WHEN** the next request sets `previous_response_id` to that prewarm and its input does not begin with the prewarm's input
- **AND** the upstream closes or rejects the anchor before `response.created`
- **THEN** the service MUST NOT replay that payload without `previous_response_id`
- **AND** the downstream client receives a retryable failure rather than a turn answered without the prewarm's context
