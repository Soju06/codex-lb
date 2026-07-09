## MODIFIED Requirements

### Requirement: Internal Responses Lite header is not forwarded upstream

The service MUST accept inbound Responses and compact requests that include `X-OpenAI-Internal-Codex-Responses-Lite`. For requests whose identity headers do not mark them as a native Codex client, the service MUST remove that header before calling upstream Responses, compact, or websocket transports. For native Codex requests that signal Responses Lite — a truthy header value, or the `ws_request_header_x_openai_internal_codex_responses_lite` key in the request's `client_metadata` — the service MUST forward the lite signal upstream on every attempt: `x-openai-internal-codex-responses-lite: true` on upstream HTTP Responses and compact requests (including retry attempts), and `ws_request_header_x_openai_internal_codex_responses_lite: "true"` inside the `client_metadata` of upstream websocket `response.create` payloads. Header matching MUST be case-insensitive. The service MUST NOT strip unrelated OpenAI SDK telemetry headers solely because they start with `x-openai-`.

#### Scenario: Non-native HTTP and compact upstream headers omit Lite

- **WHEN** a non-native client (OpenAI SDK fingerprint) sends a Responses or compact request with `X-OpenAI-Internal-Codex-Responses-Lite: 1`
- **THEN** the upstream HTTP request headers omit `x-openai-internal-codex-responses-lite`
- **AND** unrelated headers such as `x-openai-client-version` continue through the existing fingerprint policy

#### Scenario: Non-native websocket upstream headers omit Lite

- **WHEN** a non-native client opens a Responses websocket with `X-OpenAI-Internal-Codex-Responses-Lite: 1`
- **THEN** the upstream websocket connection headers omit `x-openai-internal-codex-responses-lite`
- **AND** existing websocket beta and Codex continuity headers are preserved

#### Scenario: Native Codex HTTP lite request forwards the header upstream

- **WHEN** a native Codex client sends a Responses request with `X-OpenAI-Internal-Codex-Responses-Lite: true`
- **THEN** the upstream HTTP request headers include `x-openai-internal-codex-responses-lite: true` on the initial attempt and on any HTTP retry attempt
- **AND** the upstream payload's `client_metadata` includes `ws_request_header_x_openai_internal_codex_responses_lite: "true"` so a websocket upstream transport carries the same signal

#### Scenario: Native Codex websocket lite request keeps the client-metadata key

- **WHEN** a native Codex client sends a websocket `response.create` payload whose `client_metadata` contains `ws_request_header_x_openai_internal_codex_responses_lite: "true"`
- **THEN** the upstream `response.create` payload's `client_metadata` retains that key and value

## ADDED Requirements

### Requirement: Responses Lite input payloads pass through unmodified

When an array-shaped Responses `input` contains an item with `type = "additional_tools"`, the service MUST treat the request as Responses Lite shaped and MUST forward the `input` array unmodified to upstream HTTP and websocket transports. In particular the service MUST preserve the `additional_tools` tool bundle, developer/system `message` items (which MUST NOT be lifted into top-level `instructions`), `custom_tool_call` items, and `custom_tool_call_output` items, and MUST leave top-level `instructions` unchanged. Instruction lifting for non-lite requests MUST also skip any `system`/`developer` input item whose `type` is present and is not `"message"`.

#### Scenario: additional_tools bundle reaches upstream intact

- **WHEN** a Codex client sends a Responses request whose `input` starts with `{"type": "additional_tools", "role": "developer", "tools": [...]}` followed by a developer instructions message and user content
- **THEN** the upstream request's `input` equals the inbound `input`
- **AND** top-level `instructions` keeps its inbound value

#### Scenario: Lite custom tool call items survive forwarding

- **WHEN** a lite-shaped Responses request includes `custom_tool_call` and `custom_tool_call_output` input items
- **THEN** those items reach upstream unmodified over both the HTTP route and the websocket bridge

#### Scenario: Non-lite instruction lifting is unaffected

- **WHEN** a request without an `additional_tools` item carries system or developer messages in `input`
- **THEN** their text is still lifted into top-level `instructions`
- **AND** `custom_tool_call_output` items in the same `input` are preserved
