## MODIFIED Requirements

### Requirement: WebSocket full-resend previous-response misses retry without stale anchor
When a direct WebSocket `response.create` request includes both `previous_response_id` and a self-contained full resend payload, the service MUST retain a safe replay body without `previous_response_id`. If upstream rejects the anchor with `previous_response_not_found` before `response.created`, the service MUST reconnect and replay the retained full payload as a fresh turn instead of forwarding the raw upstream invalid-request error. A payload that only carries incremental tool outputs for tool calls that are not also present in the same request is not self-contained and MUST NOT be replayed as a fresh turn without `previous_response_id`.

A client-supplied `previous_response_id` payload counts as a full resend only when the anchor is the response this downstream connection last completed and the payload's input restates that response's stored input prefix. Clients such as Codex send `previous_response_id` with only the incremental input, so for any other anchor (one this connection never completed, such as an interrupted response, an accepted prewarm, or a response from an earlier connection) the service cannot tell a delta from a full history. It MUST NOT retain or send an anchorless replay body for such a payload, and a pre-output `previous_response_not_found` MUST reach the client so that it can resend its complete history.

#### Scenario: full-resend WebSocket follow-up loses just-completed anchor
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** that anchor is the response this connection last completed
- **AND** the request payload restates that response's stored input prefix, so it carries enough input to be treated as a full resend
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

#### Scenario: delta after an anchor the connection never completed is not replayed as a fresh turn
- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses` follow-up has `previous_response_id`
- **AND** that anchor is not the response this connection last completed (for example a response that ended `response.incomplete`, an accepted `generate=false` prewarm, or a response from another connection)
- **AND** the request payload is self-contained but carries only the turn's new items, such as a user message
- **THEN** the service MUST NOT retain an anchorless replay body for that request
- **AND** if upstream emits `previous_response_not_found` before assigning a response id, the downstream client receives `previous_response_not_found` rather than a fresh turn built from the new items alone
