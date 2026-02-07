## MODIFIED Requirements

### Requirement: Support Responses input types and conversation constraints
The service MUST accept `input` as either a string or an array of input items. When `input` is a string, the service MUST normalize it into a single user input item with `input_text` content before forwarding upstream. The service MUST reject any `input_file` content part that specifies `file_id`, returning an OpenAI error envelope with `invalid_request_error`. The service MUST reject `previous_response_id` with an OpenAI error envelope because upstream does not support it. The service MUST continue to reject requests that include both `conversation` and `previous_response_id`.

#### Scenario: String input normalized to input_text item
- **WHEN** the client sends `input` as a string
- **THEN** the service accepts the request and forwards an equivalent list-based `input_text` item upstream

#### Scenario: input_file with file_id
- **WHEN** a request includes `input_file` with `file_id`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported parameter

#### Scenario: previous_response_id provided
- **WHEN** the client provides `previous_response_id`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported parameter

### Requirement: Truncation behavior on context overflow
The service MUST reject any request that includes `truncation`, returning an OpenAI error envelope indicating the unsupported parameter. The service MUST NOT forward `truncation` to upstream.

#### Scenario: truncation provided
- **WHEN** the client sends `truncation: "auto"` or `truncation: "disabled"`
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported parameter

## ADDED Requirements

### Requirement: Inline input_image URLs when possible
When a request includes `input_image` parts with HTTP(S) URLs, the service MUST attempt to fetch the image and replace the URL with a data URL if the image is within size limits. If the image cannot be fetched or exceeds size limits, the service MUST preserve the original URL and allow upstream to handle the error.

#### Scenario: input_image URL fetched
- **WHEN** the request includes an HTTP(S) `input_image` URL that is reachable and within size limits
- **THEN** the service forwards the request with the image converted to a data URL

#### Scenario: input_image URL fetch fails
- **WHEN** the request includes an HTTP(S) `input_image` URL that cannot be fetched or exceeds limits
- **THEN** the service forwards the original URL unchanged

### Requirement: Reject unsupported tool types
The service MUST reject built-in tool types that are not supported by the upstream (web search, file search, code interpreter, computer use, image generation). The service MUST return a 4xx OpenAI error envelope indicating the unsupported tool type.

#### Scenario: Unsupported tool type
- **WHEN** the client sends a tool with type `web_search_preview` (or any other unsupported built-in tool)
- **THEN** the service returns a 4xx response with an OpenAI error envelope indicating the unsupported tool type
