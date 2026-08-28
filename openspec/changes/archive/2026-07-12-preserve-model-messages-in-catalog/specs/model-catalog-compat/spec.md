## ADDED Requirements

### Requirement: Upstream model_messages is preserved in the catalog

When parsing upstream model-registry data, the system MUST preserve the
`model_messages` field on each model entry through to the Codex-native catalog
response. The field MUST NOT be stripped during fetch parsing, registry
storage, or catalog serialization. `GET /backend-api/codex/models` and
`GET /v1/models?client_version=<v>` MUST return each model's `model_messages`
object unchanged from the upstream response once a refreshed registry snapshot
exists.

#### Scenario: model_messages survives the fetch → registry → catalog path

- **GIVEN** the upstream model catalog contains a model with a `model_messages` object
- **WHEN** the model registry refresh parses the upstream response
- **THEN** the resulting `UpstreamModel.raw` includes `model_messages` unchanged

#### Scenario: Codex-native catalog endpoint returns model_messages

- **GIVEN** the model registry has a refreshed snapshot containing a model with `model_messages`
- **WHEN** a client calls `GET /backend-api/codex/models`
- **THEN** the model entry in the response includes `model_messages` with the same value as the upstream response

#### Scenario: OpenAI-compatible catalog endpoint returns model_messages for Codex clients

- **GIVEN** the model registry has a refreshed snapshot containing a model with `model_messages`
- **WHEN** a client calls `GET /v1/models?client_version=<v>`
- **THEN** the model entry in the response includes `model_messages` with the same value as the upstream response
