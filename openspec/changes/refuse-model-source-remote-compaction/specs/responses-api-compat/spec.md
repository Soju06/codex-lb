# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: OpenAI-compatible sources route only compatible public routes

OpenAI-compatible model sources SHALL be eligible for public OpenAI-compatible
routes only when the source declares support for the route shape. Chat
Completions-compatible sources MAY serve `/v1/chat/completions`.
Responses-compatible sources MAY serve `/v1/responses` and
`/backend-api/codex/responses`. Audio-transcriptions-compatible sources MAY
serve `/v1/audio/transcriptions`. Codex-native compaction, file upload,
control-plane, and websocket bridge paths MUST remain subscription-backed unless
a later requirement explicitly defines OpenAI-compatible source behavior for
those paths. A Codex-native compaction request for a model an enabled
Responses-compatible source serves MUST be refused as defined by the
"Model-source models refuse remote compaction" requirement rather than
forwarded to the source or to a subscription account.

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
  input ends with a terminal `compaction_trigger` item
- **THEN** the request is not forwarded to the external source
- **AND** the response is HTTP 400 with error type `invalid_request_error` and
  code `compaction_unsupported`
- **AND** no subscription account is selected for the request

#### Scenario: V1 compaction_trigger remains eligible for model sources

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** a client calls `POST /v1/responses` for that model whose input ends with
  a terminal `compaction_trigger` item
- **THEN** the request remains eligible for that Responses-compatible source
- **AND** it is not forced onto subscription account selection by the Codex-only
  compaction source-route exclusion

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

## ADDED Requirements

### Requirement: Model-source models refuse remote compaction

The proxy MUST refuse Codex remote compaction for a model that an enabled
Responses-capable model source serves. Such a source cannot emit a `compaction`
output item, and handing the request to a subscription account instead spends
account selection, admission, and a usage reservation on a model that never
touches that account. When a `POST /backend-api/codex/responses` request
carries a terminal `compaction_trigger`, or a
`POST /backend-api/codex/responses/compact` or `POST /v1/responses/compact`
request is received, and the requested model is served by an enabled
Responses-capable model source, the proxy MUST answer HTTP 400 with error type
`invalid_request_error` and code `compaction_unsupported` before any
subscription account selection, admission gate, or usage reservation. The
refusal MUST NOT create a request log entry for a dispatch that never happened.

Source ownership SHALL be decided by the ordinary Responses source-selection
rules: the raw client alias then the normalized model, the API key model
allowlist, the source assignment scope, and subscription-registry precedence.
A subscription-registry model that an unscoped API key never source-routes
MUST keep reaching the subscription compact flow even when a source lists the
same slug. A model served only by a disabled source or a disabled source model
MUST be refused at the same boundary with the existing HTTP 503
`model_source_disabled` response, so a compaction request never hands a
source-owned slug to a subscription account.

The 400 status is deliberate: the Codex CLI treats `invalid_request_error` as
non-retryable and falls back to local compaction, whereas a 429 or 5xx invites
retries against a path that can never succeed.

#### Scenario: Standalone compact request for a source model is refused

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **WHEN** a client calls `POST /backend-api/codex/responses/compact` or
  `POST /v1/responses/compact` with model `deepseek-v4-flash`
- **THEN** the response is HTTP 400 with error type `invalid_request_error` and
  code `compaction_unsupported`
- **AND** the source upstream receives no request
- **AND** no subscription account is selected and no usage reservation is held

#### Scenario: Source model without a trigger still routes to the source

- **GIVEN** an enabled Responses-compatible source exposes model `deepseek-v4-flash`
- **WHEN** a client calls `POST /backend-api/codex/responses` for that model
  without a `compaction_trigger` item
- **THEN** the proxy forwards the request to that source's Responses endpoint

#### Scenario: Compaction for a disabled-source model is refused as disabled

- **GIVEN** a Responses-compatible source exposes model `deepseek-v4-flash`
- **AND** the source is disabled
- **WHEN** a client calls `POST /backend-api/codex/responses` for that model
  with a terminal `compaction_trigger` item, or `POST /backend-api/codex/responses/compact`
  or `POST /v1/responses/compact` with that model
- **THEN** the response is HTTP 503 with code `model_source_disabled`
- **AND** no subscription account is selected and no usage reservation is held

#### Scenario: Subscription model keeps the compact flow

- **GIVEN** model `gpt-5.6-sol` is in the subscription model registry
- **AND** an enabled Responses-compatible source also lists `gpt-5.6-sol`
- **WHEN** an unscoped client calls `POST /backend-api/codex/responses` for that
  model with a terminal `compaction_trigger` item
- **THEN** the request reaches subscription account selection for the compact flow
- **AND** it is not refused with `compaction_unsupported`
