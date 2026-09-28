## MODIFIED Requirements

### Requirement: Map chat requests to Responses wire format

The service MUST map chat messages into the Responses request format by merging `system`/`developer` content into `instructions` and forwarding all other messages as `input`. When `response_format.type` is `json_object`, a merged `system`/`developer` message mentioned JSON, and no `user` message mentions JSON, the mapped `input` MUST start its first `user` message with the text part `Respond in JSON.`, because upstream JSON mode requires a user input message to mention JSON. Tool definitions MUST be normalized to the Responses tool schema, and `tool_choice`, `reasoning_effort`, and `response_format` MUST be mapped consistently. Unsupported fields MUST not be silently ignored if they change behavior.

#### Scenario: System message normalization
- **WHEN** the client sends a `system` message followed by a `user` message
- **THEN** the service maps the system content to `instructions` and the user message to `input`

#### Scenario: JSON object response format preserves instruction-role messages
- **WHEN** the client sends `response_format: {"type":"json_object"}` with a `system` or `developer` message that instructs JSON output
- **AND** no `user` message mentions JSON
- **THEN** the mapped `instructions` contain that message content
- **AND** the first mapped `user` message starts with the text part `Respond in JSON.`

#### Scenario: JSON object response format with a user JSON mention
- **WHEN** the client sends `response_format: {"type":"json_object"}` and a `user` message mentions JSON
- **THEN** the mapped `input` is not changed

#### Scenario: Tool choice values
- **WHEN** the client sets `tool_choice` to `none`, `auto`, or `required`
- **THEN** the service forwards the value consistently in the mapped Responses request
