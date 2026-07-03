## ADDED Requirements

### Requirement: OpenAI-compatible sources route only compatible public routes

OpenAI-compatible model sources SHALL be eligible for public OpenAI-compatible
routes only when the source declares support for the route shape. Chat
Completions-compatible sources MAY serve `/v1/chat/completions`.
Responses-compatible sources MAY serve `/v1/responses` and
`/backend-api/codex/responses`. Codex-native compaction, file upload,
control-plane, and websocket bridge paths MUST remain subscription-backed unless
a later requirement explicitly defines OpenAI-compatible source behavior for
those paths.

#### Scenario: Chat completions routes to OpenAI-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares chat-completions support
- **AND** the authenticated API key is allowed to use that source/model
- **WHEN** the client calls `POST /v1/chat/completions` with that model
- **THEN** the proxy forwards the request to the source's configured base URL
  using the source's upstream API key

#### Scenario: Codex-native Responses route uses Responses-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares Responses support
- **AND** it exposes model `deepseek-v4-flash`
- **WHEN** a client calls `POST /backend-api/codex/responses` with model `deepseek-v4-flash`
- **THEN** the proxy forwards the request to that source's Responses endpoint

#### Scenario: Chat-only source is not used for Codex-native Responses route

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** the source declares Chat Completions support only
- **WHEN** a client calls `POST /backend-api/codex/responses` with model `local-coder`
- **THEN** the request is not routed to that source
- **AND** subscription-backed Codex routing rules continue to apply

### Requirement: Source-routed chat payloads are sanitized before forwarding

Source-routed `/v1/chat/completions` requests SHALL forward the client's
OpenAI-compatible payload with the following sanitization applied to the
outbound body:

- An empty `tools` array MUST be omitted, together with `tool_choice` and
  `parallel_tool_calls`, so tool-less requests reach the source without
  tool-calling artifacts.
- Non-standard reasoning toggles (`include_reasoning`, `separate_reasoning`,
  `stream_reasoning`, `reasoning`, and `reasoning_effort`) MUST be stripped
  unless the source model's catalog entry opts into reasoning via
  `raw_metadata_json` containing `"supports_reasoning": true`.
- An API key's enforced reasoning effort MAY still be applied after
  sanitization; explicit operator policy overrides the default strip.

#### Scenario: Empty tools array is not forwarded

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **WHEN** a client calls `POST /v1/chat/completions` for that model without
  tools (or with `"tools": []`) and `"tool_choice": "none"`
- **THEN** the body forwarded to the source contains no `tools`, `tool_choice`,
  or `parallel_tool_calls` keys

#### Scenario: Reasoning toggles are stripped for non-reasoning source models

- **GIVEN** a source model whose catalog entry does not declare
  `"supports_reasoning": true`
- **WHEN** a client sends `include_reasoning`, `separate_reasoning`,
  `stream_reasoning`, `reasoning`, or `reasoning_effort` in the request
- **THEN** none of those keys appear in the body forwarded to the source

#### Scenario: Catalog opt-in preserves reasoning toggles

- **GIVEN** a source model whose `raw_metadata_json` contains
  `"supports_reasoning": true`
- **WHEN** a client sends `include_reasoning: true`
- **THEN** the forwarded body preserves the client's reasoning fields
