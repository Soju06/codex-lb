## ADDED Requirements

### Requirement: OpenAI-compatible sources route only compatible public routes

OpenAI-compatible model sources SHALL be eligible for public OpenAI-compatible
routes only when the source declares support for the route shape. Chat
Completions-compatible sources MAY serve `/v1/chat/completions`.
Responses-compatible sources MAY serve `/v1/responses` and
`/backend-api/codex/responses`. Audio-transcriptions-compatible sources MAY
serve `/v1/audio/transcriptions`. Codex-native compaction, file upload,
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

#### Scenario: Compaction request is not source-routed

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `POST /backend-api/codex/responses` for that model whose
  input contains a `compaction_trigger` item
- **THEN** the request is not forwarded to the external source
- **AND** it follows the subscription-backed Codex compaction path instead

#### Scenario: File-referencing request is not source-routed

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `/backend-api/codex/responses` or `/v1/responses` for that
  model whose input references an uploaded `input_file`/`input_image` `file_id`
- **THEN** the request is not forwarded to the external source
- **AND** it follows the subscription path so the account-scoped file pin is honored

#### Scenario: Audio transcription routes to OpenAI-compatible source

- **GIVEN** an enabled OpenAI-compatible source declares audio transcriptions support
- **AND** it exposes model `whisper-large-v3`
- **WHEN** the client calls `POST /v1/audio/transcriptions` with multipart
  field `model=whisper-large-v3`
- **THEN** the proxy forwards the multipart request to the source's
  `/audio/transcriptions` endpoint
- **AND** the request uses the source's upstream API key

#### Scenario: Non-source transcription model keeps subscription validation

- **GIVEN** no audio-transcriptions-compatible source exposes model `gpt-4o-mini`
- **WHEN** the client calls `POST /v1/audio/transcriptions` with
  `model=gpt-4o-mini`
- **THEN** the proxy returns the existing unsupported transcription model error

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

### Requirement: Source-routed audio transcriptions preserve OpenAI-compatible multipart semantics

Source-routed `/v1/audio/transcriptions` requests SHALL forward the inbound
audio file and non-file multipart fields to the selected source's
`/audio/transcriptions` endpoint. The proxy MUST use the stored source API key
for upstream authorization and MUST NOT forward the downstream client's
authorization credential. JSON and non-JSON successful upstream response bodies
SHALL be returned to the client with the upstream content type when present.

#### Scenario: Text transcription response passes through

- **GIVEN** an enabled OpenAI-compatible source exposes model `whisper-large-v3`
- **AND** the client requests `response_format=text`
- **WHEN** the source returns a plain text response
- **THEN** the proxy returns that response body without requiring JSON parsing

#### Scenario: Limited key requires token usage

- **GIVEN** an API key has token or cost limits
- **AND** a source-routed audio transcription response has no token-compatible
  usage fields
- **AND** the source model declares no per-minute audio rate
- **WHEN** the upstream source returns a successful transcription response
- **THEN** the proxy releases the reservation
- **AND** returns `usage_unavailable` instead of allowing unaccounted limited-key usage

### Requirement: Audio transcription sources MAY bill by duration

The proxy SHALL support per-minute audio billing for source models that
declare an `audio_per_minute` rate. When the rate is set and a source-routed
`/v1/audio/transcriptions` response carries a positive audio duration
(top-level `duration` seconds, or a `usage.seconds`/`usage.duration` fallback),
the proxy MUST settle cost as `duration_minutes * audio_per_minute` with zero
tokens, and MUST record that cost on the request log and against the API key's
`cost_usd` limit. Duration billing MUST take precedence over token pricing on
the transcription route. A model with no `audio_per_minute` rate MUST fall back
to token-usage settlement.

#### Scenario: Duration-priced model settles cost from audio length

- **GIVEN** an audio-transcriptions source model with `audio_per_minute = 0.30`
- **AND** an API key with a `cost_usd` limit
- **WHEN** a transcription response reports `duration = 120` seconds and no token usage
- **THEN** the API-key reservation is finalized with 0 tokens and $0.60 cost
- **AND** the request log records `cost_usd = 0.60`

#### Scenario: Duration billing does not require token usage for limited keys

- **GIVEN** an audio-transcriptions source model with an `audio_per_minute` rate
- **AND** an API key with token or cost limits
- **WHEN** a transcription response carries a positive duration but no token usage
- **THEN** the request succeeds and settles from duration
- **AND** the proxy does not return `usage_unavailable`
