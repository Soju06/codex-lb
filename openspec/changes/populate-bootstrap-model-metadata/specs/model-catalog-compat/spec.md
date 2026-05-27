## ADDED Requirements

### Requirement: Bootstrap model catalog is available before refresh

Before the first successful upstream model-registry refresh, the system MUST
serve a conservative static catalog of known Codex model slugs from both
`GET /v1/models` and `GET /backend-api/codex/models`. The bootstrap catalog MUST
include `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`,
`gpt-5.3-codex-spark`, `gpt-5.2`, and `codex-auto-review`, and MUST NOT invent
unverified variant slugs such as `gpt-5.5-pro`.

#### Scenario: OpenAI-compatible models endpoint serves bootstrap slugs

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /v1/models`
- **THEN** the response contains exactly the bootstrap model slugs
- **AND** the response does not include `gpt-5.5-pro`

#### Scenario: Codex-native models endpoint serves bootstrap metadata

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** entries such as `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex-spark`, and `codex-auto-review` include representative upstream metadata including client version, context-window, visibility, modality, plan-availability, and reasoning/verbosity fields where known

### Requirement: Refreshed upstream model data remains authoritative

The system MUST treat a refreshed upstream model-registry snapshot as
authoritative over the static bootstrap catalog. Once that snapshot exists,
model catalog endpoints and model-behavior lookups MUST use the refreshed
snapshot instead of the static bootstrap catalog. Before refresh, websocket
preference lookup MUST use bootstrap model metadata when the requested slug
matches a bootstrap entry.

#### Scenario: Refreshed snapshot replaces bootstrap catalog

- **GIVEN** the model registry has a refreshed upstream snapshot
- **WHEN** a client calls `GET /v1/models` or `GET /backend-api/codex/models`
- **THEN** the response is built from the refreshed snapshot
- **AND** bootstrap-only entries are not added to the response

#### Scenario: Bootstrap websocket preference is honored before refresh

- **GIVEN** the model registry has no refreshed upstream snapshot
- **WHEN** websocket preference is checked for a bootstrap model marked as websocket-preferred
- **THEN** the lookup returns that bootstrap preference
