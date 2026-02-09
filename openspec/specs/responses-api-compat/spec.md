# Responses API Compatibility

## Purpose

Ensure `/v1/responses` behavior matches OpenAI Responses API expectations for request validation, streaming events, and error envelopes within upstream constraints.
## Requirements
### Requirement: Validate Responses create requests
The service MUST accept POST requests to `/v1/responses` with a JSON body and MUST validate required fields according to OpenAI Responses API expectations. The request MUST include `model` and `input`, MAY omit `instructions`, MUST reject mutually exclusive fields (`input` and `messages` when both are present), and MUST reject `store=true` with an OpenAI error envelope.

#### Scenario: Minimal valid request
- **WHEN** the client sends `{ "model": "gpt-4.1", "input": "hi" }`
- **THEN** the service accepts the request and begins a response (streaming or non-streaming based on `stream`)

#### Scenario: Invalid request fields
- **WHEN** the client omits `model` or `input`, or sends both `input` and `messages`
- **THEN** the service returns a 4xx response with an OpenAI error envelope describing the invalid parameter

### Requirement: Support Responses input types and conversation constraints
The service MUST accept `input` as either a string or an array of input items. When `input` is a string, the service MUST normalize it into a single user input item with `input_text` content before forwarding upstream. The service MUST reject `previous_response_id` with an OpenAI error envelope because upstream does not support it. The service MUST continue to reject requests that include both `conversation` and `previous_response_id`.

#### Scenario: String input
- **WHEN** the client sends `input` as a string
- **THEN** the request is accepted and forwarded as a single `input_text` item

#### Scenario: Array input items
- **WHEN** the client sends `input` as an array of input items
- **THEN** the request is accepted and each item is forwarded in order

#### Scenario: conversation and previous_response_id conflict
- **WHEN** the client provides both `conversation` and `previous_response_id`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating invalid parameters

#### Scenario: previous_response_id provided
- **WHEN** the client provides `previous_response_id`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported parameter

### Requirement: Reject input_file file_id in Responses
The service MUST reject `input_file.file_id` in Responses input items and return a 4xx OpenAI invalid_request_error with message "Invalid request payload".

#### Scenario: input_file file_id rejected
- **WHEN** a request includes an input item with `{"type":"input_file","file_id":"file_123"}`
- **THEN** the service returns a 4xx OpenAI invalid_request_error with message "Invalid request payload" and param `input`

### Requirement: Stream Responses events with terminal completion
When `stream=true`, the service MUST respond with `text/event-stream` and emit OpenAI Responses streaming events. The stream MUST include a terminal event of `response.completed` or `response.failed`. If upstream closes the stream without a terminal event, the service MUST emit `response.failed` with a stable error code indicating an incomplete stream.

#### Scenario: Successful streaming completion
- **WHEN** the upstream emits `response.completed`
- **THEN** the service forwards the event and closes the stream

#### Scenario: Missing terminal event
- **WHEN** the upstream closes the stream without `response.completed` or `response.failed`
- **THEN** the service emits `response.failed` with an error code indicating an incomplete stream and closes the stream

### Requirement: Responses streaming event taxonomy
When streaming, the service MUST forward the standard Responses streaming event types, including `response.created`, `response.in_progress`, and `response.completed`/`response.failed` as applicable, preserving event order and `sequence_number` fields when present.

#### Scenario: response.created and response.in_progress present
- **WHEN** the upstream emits `response.created` followed by `response.in_progress`
- **THEN** the service forwards both events in order without mutation

### Requirement: Non-streaming Responses return a full response object
When `stream` is `false` or omitted, the service MUST return a JSON response object consistent with OpenAI Responses API, including `id`, `object: "response"`, `status`, `output`, and `usage` when available.

#### Scenario: Non-streaming response
- **WHEN** the client sends a valid request with `stream=false`
- **THEN** the service returns a single JSON response object containing output items and status

### Requirement: Error envelope parity for invalid or unsupported requests
For invalid inputs or unsupported features, the service MUST return an OpenAI-style error envelope (`{ "error": { ... } }`) with stable `type`, `code`, and `param` fields. For streaming requests, errors MUST be emitted as `response.failed` events containing the same error envelope.

#### Scenario: Unsupported feature flag
- **WHEN** the client sets an unsupported feature (e.g., `store=true`)
- **THEN** the service returns an OpenAI error envelope (or `response.failed` for streaming) with a stable error code and message

### Requirement: Validate include values
If the client supplies `include`, the service MUST accept only values documented by the Responses API and MUST return a 4xx OpenAI error envelope for unknown include values.

#### Scenario: Known include value
- **WHEN** the client includes `message.output_text.logprobs`
- **THEN** the service accepts the request and includes logprobs in the response output when available

#### Scenario: Unknown include value
- **WHEN** the client includes an unsupported include value
- **THEN** the service returns a 4xx OpenAI error envelope indicating the invalid include entry

### Requirement: Allow web_search tools and reject unsupported built-ins
The service MUST accept Responses requests that include tools with type `web_search` or `web_search_preview`. The service MUST normalize `web_search_preview` to `web_search` before forwarding upstream. The service MUST reject other built-in tool types (file_search, code_interpreter, computer_use, computer_use_preview, image_generation) with an OpenAI invalid_request_error.

#### Scenario: web_search_preview tool accepted
- **WHEN** the client sends `tools=[{"type":"web_search_preview"}]`
- **THEN** the service accepts the request and forwards the tool as `web_search`

#### Scenario: unsupported built-in tool rejected
- **WHEN** the client sends `tools=[{"type":"code_interpreter"}]`
- **THEN** the service returns a 4xx response with an OpenAI invalid_request_error indicating the unsupported tool type

### Requirement: Inline input_image URLs when possible
When a request includes `input_image` parts with HTTP(S) URLs, the service MUST attempt to fetch the image and replace the URL with a data URL if the image is within size limits. If the image cannot be fetched or exceeds size limits, the service MUST preserve the original URL and allow upstream to handle the error.

#### Scenario: input_image URL fetched
- **WHEN** the request includes an HTTP(S) `input_image` URL that is reachable and within size limits
- **THEN** the service forwards the request with the image converted to a data URL

#### Scenario: input_image URL fetch fails
- **WHEN** the request includes an HTTP(S) `input_image` URL that cannot be fetched or exceeds limits
- **THEN** the service forwards the original URL unchanged

### Requirement: Reject truncation
The service MUST reject any request that includes `truncation`, returning an OpenAI error envelope indicating the unsupported parameter. The service MUST NOT forward `truncation` to upstream.

#### Scenario: truncation provided
- **WHEN** the client sends `truncation: "auto"` or `truncation: "disabled"`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported parameter

### Requirement: Tool call events and output items are preserved
If the upstream model emits tool call deltas or output items, the service MUST forward those events in streaming mode and MUST include tool call items in the final response output for non-streaming mode.

#### Scenario: Tool call emitted
- **WHEN** the upstream emits a tool call delta event
- **THEN** the service forwards the delta event and includes the finalized tool call in the completed response output

### Requirement: Usage mapping and propagation
When usage data is provided by the upstream, the service MUST include `input_tokens`, `output_tokens`, and `total_tokens` (and token detail fields if present) in `response.completed` events and in non-streaming responses.

#### Scenario: Usage included
- **WHEN** the upstream includes usage in `response.completed`
- **THEN** the service forwards usage fields in the completed event and in the final response object
