## ADDED Requirements

### Requirement: Model catalog entries preserve model-source identity

The model registry SHALL represent each catalog entry with explicit model-source
identity. Subscription-backed entries SHALL use a subscription source kind and
MUST continue to derive account/plan availability from the existing ChatGPT
account model registry refresh. OpenAI-compatible endpoint entries SHALL use an
OpenAI-compatible source kind and a stable source id. The model-source
abstraction MUST NOT require OpenAI-compatible sources to be represented as
`Account` rows.

#### Scenario: Subscription model keeps subscription source identity

- **WHEN** the existing model refresh loads `gpt-5.4` from ChatGPT/Codex account metadata
- **THEN** the registry entry has source kind `subscription`
- **AND** the entry remains eligible for existing account/plan routing

#### Scenario: OpenAI-compatible model keeps endpoint source identity

- **WHEN** an enabled OpenAI-compatible source defines model `local-coder`
- **THEN** the registry entry has source kind `openai_compatible`
- **AND** the entry references the source id for that endpoint
- **AND** no `Account` row is required for that source

### Requirement: /v1/models includes eligible OpenAI-compatible source models

`GET /v1/models` SHALL include enabled OpenAI-compatible source models alongside
subscription-backed public models when the authenticated API key is allowed to
see the model and source. Disabled sources and disabled source models MUST NOT be
listed. Source identity MAY be omitted from the public OpenAI-compatible model
payload, but internal filtering and routing MUST preserve it.

#### Scenario: API key sees assigned source model

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** an API key is assigned to that source and allows `local-coder`
- **WHEN** the key calls `GET /v1/models`
- **THEN** the response includes `local-coder`

#### Scenario: API key cannot see unassigned source model

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** an API key is scoped to a different source
- **WHEN** the key calls `GET /v1/models`
- **THEN** the response does not include `local-coder`

### Requirement: Codex-native catalog includes only Responses-capable source models

`GET /backend-api/codex/models` SHALL include OpenAI-compatible source models
only when the source explicitly declares Responses-compatible support. This
allows Codex model-picker entries for external providers without advertising
Chat Completions-only sources that cannot satisfy Codex-native Responses
requests. Disabled sources and disabled source models MUST NOT be listed.
Subscription-backed Codex catalog entries MUST continue to be listed through the
existing registry path. If a source model entry emits `model_provider`, it MUST
emit `codex-lb` and MUST NOT advertise the external upstream provider name.

#### Scenario: Responses-capable source is advertised to Codex-native clients

- **GIVEN** an enabled OpenAI-compatible source exposes model `deepseek-v4-flash`
- **AND** the source declares Responses-compatible support
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the response includes `deepseek-v4-flash`
- **AND** the model entry does not change the Codex provider away from `codex-lb`

#### Scenario: Chat-only source is not advertised to Codex-native clients

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **AND** the source declares Chat Completions support only
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the response does not include `local-coder`
