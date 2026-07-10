# model-catalog-compat — Delta

## ADDED Requirements

### Requirement: Codex clients receive the Codex catalog from /v1/models

When `GET /v1/models` is called with a non-empty `client_version` query
parameter, the service MUST return the same Codex catalog payload as
`GET /backend-api/codex/models`, including its `models` entries and the
OpenAI-compatible `object`/`data` fields. When the parameter is absent or
empty, the service MUST return the unchanged OpenAI-compatible list shape.
API-key model filtering and visibility rules MUST apply in both cases.

#### Scenario: Codex client fetches its catalog through the /v1 base URL

- **GIVEN** a Codex client configured with `openai_base_url` pointing at this proxy
- **WHEN** it calls `GET /v1/models?client_version=0.144.1`
- **THEN** the response contains Codex catalog entries under `models`
- **AND** the payload equals the response of `GET /backend-api/codex/models`

#### Scenario: OpenAI-compatible clients are unaffected

- **GIVEN** an OpenAI-compatible client
- **WHEN** it calls `GET /v1/models` without a `client_version` parameter (or with an empty value)
- **THEN** the response keeps the `{"object": "list", "data": [...]}` shape
