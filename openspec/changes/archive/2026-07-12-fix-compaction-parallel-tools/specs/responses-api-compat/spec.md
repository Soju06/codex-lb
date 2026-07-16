## MODIFIED Requirements

### Requirement: Compact requests drop tool-only fields
The service SHALL remove `tools` and `tool_choice` from compact request payloads, and set `parallel_tool_calls` to `false`, before calling the upstream compact endpoint.

#### Scenario: compact request reuses a full Responses payload shape

- **WHEN** a client sends `/backend-api/codex/responses/compact` or `/v1/responses/compact` with `tools`, `tool_choice`, or `parallel_tool_calls`
- **THEN** the proxy drops `tools` and `tool_choice` before the upstream compact request
- **AND** the proxy sends `parallel_tool_calls` as `false`
- **AND** the compact request continues without a local or upstream `invalid_request_error` caused by `param="tools"`
