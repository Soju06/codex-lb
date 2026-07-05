## ADDED Requirements

### Requirement: Dashboard manages OpenAI-compatible model sources

The Settings page SHALL provide an operator control surface for
OpenAI-compatible model sources. Operators SHALL be able to create a source with
a name, base URL, optional upstream API key, route-shape support flags, and one
or more model ids. Operators SHALL be able to enable, disable, and delete
sources. The dashboard MUST NOT expose decrypted upstream source API keys after
creation.

#### Scenario: Operator creates a vLLM model source

- **WHEN** an operator submits a model source with base URL `http://localhost:8000/v1`
- **AND** model id `local-coder`
- **THEN** the dashboard calls `POST /api/model-sources/`
- **AND** the new source appears in the Settings model-source list

#### Scenario: Operator disables a model source

- **WHEN** an operator toggles an enabled model source off
- **THEN** the dashboard calls `PATCH /api/model-sources/{sourceId}` with `isEnabled=false`
- **AND** the source remains listed as disabled

### Requirement: Dashboard model picker includes source models

The dashboard model listing endpoint (`GET /api/models`) SHALL include enabled
OpenAI-compatible source models alongside subscription registry models so
API-key model allowlists can reference source models. Duplicate slugs MUST be
listed once with the subscription entry taking precedence.

#### Scenario: Allowed-models picker offers a source model

- **GIVEN** an enabled OpenAI-compatible source exposes model `local-coder`
- **WHEN** the dashboard requests `GET /api/models`
- **THEN** the response includes `local-coder`
- **AND** an API key allowlisted to `local-coder` can call it through the proxy

### Requirement: Dashboard API-key forms assign model sources

The API-key create and edit dialogs SHALL allow operators to assign zero or
more model sources separately from account assignments. Selecting no model
sources SHALL mean all eligible sources are allowed subject to model allowlists
and route compatibility.

#### Scenario: Create key scoped to a model source

- **WHEN** an operator creates an API key and selects model source `src_vllm`
- **THEN** the dashboard sends `assignedSourceIds=["src_vllm"]`
- **AND** the API key response preserves the assigned source id

#### Scenario: Edit key clears source scope

- **WHEN** an API key has assigned source ids
- **AND** an operator clears the source selection
- **THEN** the dashboard sends `assignedSourceIds=[]`
