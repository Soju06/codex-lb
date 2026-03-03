## ADDED Requirements

### Requirement: Sanitize unsupported interleaved and legacy chat input fields
Before forwarding Responses requests upstream, the service MUST remove unsupported interleaved reasoning and legacy chat fields from `input` items and content parts. The service MUST strip `reasoning_content`, `reasoning_details`, `tool_calls`, and `function_call` fields when they appear in `input` structures, and MUST remove unsupported reasoning-only content parts that are not accepted by upstream.

#### Scenario: Interleaved reasoning and legacy chat fields in input item
- **WHEN** a request includes an input item containing `reasoning_content`, `reasoning_details`, `tool_calls`, or `function_call`
- **THEN** the service strips those fields before forwarding upstream

#### Scenario: Unsupported reasoning-only content part in input
- **WHEN** a request includes a content part that represents interleaved reasoning-only payload
- **THEN** the service removes that content part before forwarding upstream

### Requirement: Preserve supported top-level reasoning controls
When sanitizing interleaved reasoning input fields, the service MUST preserve supported top-level reasoning controls (`reasoning.effort`, `reasoning.summary`) and continue forwarding them unchanged.

#### Scenario: Top-level reasoning with interleaved input fields
- **WHEN** a request includes top-level `reasoning` plus interleaved reasoning fields inside `input`
- **THEN** top-level `reasoning` is preserved while unsupported `input` fields are removed
### Requirement: Normalize assistant text content part types for upstream compatibility
Before forwarding Responses requests upstream, the service MUST normalize assistant-role text content parts in `input` so they use `output_text` (not `input_text`) to satisfy upstream role-specific validation.

#### Scenario: Assistant input message uses input_text
- **WHEN** a request includes an `input` message with `role: "assistant"` and a text content part typed as `input_text`
- **THEN** the service rewrites that content part type to `output_text` before forwarding upstream



### Requirement: Normalize tool message history for upstream compatibility
Before forwarding Responses requests upstream, the service MUST normalize tool-role message history into Responses-native function call output items. Tool messages MUST include a non-empty call identifier and MUST be rewritten as `type: "function_call_output"` with the same call identifier.

#### Scenario: Tool message in conversation history
- **WHEN** a request includes a message with `role: "tool"`, `tool_call_id`, and text content
- **THEN** the service rewrites it to a `function_call_output` input item using `call_id` and tool output text before forwarding upstream

### Requirement: Reject unsupported message roles with client errors
When coercing v1 `messages` into Responses input, the service MUST reject messages that do not include a string role or use an unsupported role value.

#### Scenario: Unsupported message role
- **WHEN** a request includes a message role outside the supported set
- **THEN** the service returns a client-facing invalid payload error referencing `messages`
