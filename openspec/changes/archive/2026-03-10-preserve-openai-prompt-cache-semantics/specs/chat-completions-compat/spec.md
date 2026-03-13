## MODIFIED Requirements

### Requirement: Map chat requests to Responses wire format
The service MUST map chat messages into the Responses request format by merging `system`/`developer` content into `instructions` and forwarding all other messages as `input`. Tool definitions MUST be normalized to the Responses tool schema, and `tool_choice`, `reasoning_effort`, `response_format`, and OpenAI prompt cache controls MUST be mapped consistently. Unsupported fields MUST not be silently ignored if they change behavior.

#### Scenario: System message normalization
- **WHEN** the client sends a `system` message followed by a `user` message
- **THEN** the service maps the system content to `instructions` and the user message to `input`

#### Scenario: Tool choice values
- **WHEN** the client sets `tool_choice` to `none`, `auto`, or `required`
- **THEN** the service forwards the value consistently in the mapped Responses request

#### Scenario: Prompt cache controls preserved in chat mapping
- **WHEN** a client sends `/v1/chat/completions` with `prompt_cache_key` and `prompt_cache_retention`
- **THEN** the mapped Responses payload preserves `prompt_cache_key` and strips `prompt_cache_retention`
