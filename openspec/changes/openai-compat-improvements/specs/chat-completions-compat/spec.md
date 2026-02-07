## MODIFIED Requirements

### Requirement: Map chat requests to Responses wire format
The service MUST map chat messages into the Responses request format by merging `system`/`developer` content into `instructions` and forwarding all other messages as `input`. User content parts MUST be normalized to Responses-compatible parts: `text` → `input_text`, `image_url` → `input_image`, `input_audio` → `input_file` (data URL), and `file` → `input_file` (data URL or file URL). If a `file` part includes `file_id`, the service MUST reject the request with a 4xx OpenAI error envelope indicating the unsupported parameter. Tool definitions MUST be normalized to the Responses tool schema, and `tool_choice`, `reasoning_effort`, and `response_format` MUST be mapped consistently. Unsupported fields MUST not be silently ignored if they change behavior.

#### Scenario: System message normalization
- **WHEN** the client sends a `system` message followed by a `user` message
- **THEN** the service maps the system content to `instructions` and the user message to `input`

#### Scenario: Multimodal user content mapping
- **WHEN** a `user` message includes text and image content parts
- **THEN** the service forwards them as `input_text` and `input_image` parts in order

#### Scenario: Audio content mapping
- **WHEN** a `user` message includes `input_audio`
- **THEN** the service maps it to an `input_file` data URL in the Responses input

#### Scenario: File content with file_id
- **WHEN** a `user` message includes a `file` part with `file_id`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported parameter
