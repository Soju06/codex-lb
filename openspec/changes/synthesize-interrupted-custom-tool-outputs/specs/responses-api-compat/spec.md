# responses-api-compat — Delta

## ADDED Requirements

### Requirement: Interrupted tool outputs are synthesized for all tool call item types

When a completed upstream response contains tool call items (`function_call`,
`custom_tool_call`, or `apply_patch_call`) whose outputs were never returned by
the client, and a subsequent request references that response via
`previous_response_id`, the service MUST inject synthetic interrupted-output
items of the matching output type (`function_call_output`,
`custom_tool_call_output`, `apply_patch_call_output`) so upstream does not
reject the request. The upstream error message variants for all three call
types MUST be classified as missing-tool-output errors so existing recovery
paths apply.

#### Scenario: interrupted custom tool call is synthesized

- **WHEN** a turn ends with an unresolved `custom_tool_call` and the next
  request references that response via `previous_response_id` without the
  matching `custom_tool_call_output`
- **THEN** the service injects a synthetic `custom_tool_call_output` for the
  pending call id before forwarding upstream

#### Scenario: custom variant of the upstream 400 is classified

- **WHEN** upstream returns `invalid_request_error` with `param=input` and a
  message starting with "No tool output found for custom tool call call_"
- **THEN** the service treats it as a missing-tool-output error, engaging the
  same masking and retry recovery as the function-call variant
