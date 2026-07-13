## ADDED Requirements

### Requirement: WebSocket tool-output deltas are not fresh-retryable

The service MUST NOT replay a direct WebSocket Responses request as a fresh turn
without the previous-response anchor when it includes `previous_response_id` and
only carries tool output items for tool calls that are not present in the same
payload after an upstream `previous_response_not_found`.

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn

- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses`
  follow-up has `previous_response_id`
- **AND** the request payload carries `function_call_output`,
  `custom_tool_call_output`, or `apply_patch_call_output` items without their
  matching tool-call items in the same payload
- **AND** upstream emits `previous_response_not_found` before assigning a
  response id
- **THEN** the service MUST NOT replay that payload as a fresh turn without
  `previous_response_id`
