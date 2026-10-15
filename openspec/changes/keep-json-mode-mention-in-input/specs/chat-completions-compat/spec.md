## MODIFIED Requirements

### Requirement: Map chat requests to Responses wire format

The service MUST map chat messages into the Responses request format by merging `system`/`developer` content into `instructions` and forwarding all other messages as `input`. When `response_format.type` is `json_object`, a `system` or `developer` message that mentions JSON MUST instead stay in `input` in its original position with the `developer` role, because upstream JSON mode only counts a JSON mention inside input messages and rejects the `system` role there. Tool definitions MUST be normalized to the Responses tool schema, and `tool_choice`, `reasoning_effort`, and `response_format` MUST be mapped consistently. Unsupported fields MUST not be silently ignored if they change behavior.

#### Scenario: System message normalization
- **WHEN** the client sends a `system` message followed by a `user` message
- **AND** the request does not use `response_format.type = "json_object"`
- **THEN** the service maps the system content to `instructions` and the user message to `input`

#### Scenario: JSON object response format preserves instruction-role messages
- **WHEN** the client sends `response_format: {"type":"json_object"}` with a `system` or `developer` message that instructs JSON output
- **THEN** the mapped Responses payload keeps that message in `input` with the `developer` role
- **AND** the mapped `instructions` value does not contain that message content

#### Scenario: JSON object response format with an instruction that does not mention JSON
- **WHEN** the client sends `response_format: {"type":"json_object"}` and a `system` message that does not mention JSON
- **THEN** the service maps that system content to `instructions`

#### Scenario: Tool choice values
- **WHEN** the client sets `tool_choice` to `none`, `auto`, or `required`
- **THEN** the service forwards the value consistently in the mapped Responses request
