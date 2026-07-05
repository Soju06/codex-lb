## ADDED Requirements

### Requirement: API keys may be scoped to model sources

The system SHALL allow API keys to be scoped to zero or more model-source ids in
addition to existing account assignments and model allowlists. Source scoping
MUST be represented separately from account assignment scoping and MUST expose a
source-assignment-scope-enabled state in API-key read contracts. When an API key
has source assignment scope disabled, it MAY use any enabled source subject to
model allowlists and route eligibility. When source assignment scope is enabled,
source-routed requests and model listing MUST be restricted to the assigned
source ids.

#### Scenario: Key without source assignments can see enabled source models

- **GIVEN** an API key has no assigned source ids
- **AND** source assignment scope is disabled
- **AND** its model allowlist permits `local-coder`
- **WHEN** the key calls `GET /v1/models`
- **THEN** enabled `local-coder` source entries are eligible for listing

#### Scenario: Key with source assignments is restricted

- **GIVEN** an API key is assigned to source `src_a`
- **AND** source `src_b` also exposes model `local-coder`
- **WHEN** the key calls `GET /v1/models`
- **THEN** only entries from `src_a` are eligible

#### Scenario: Deleted assigned source does not broaden access

- **GIVEN** an API key is assigned to source `src_a`
- **AND** source `src_b` also exposes model `local-coder`
- **WHEN** `src_a` is deleted
- **THEN** the API key remains source-assignment scoped with no assigned source ids
- **AND** source `src_b` is not eligible for model listing or routing

### Requirement: Source-routed usage uses API-key reservations

The system MUST reserve API-key usage before forwarding an OpenAI-compatible
source-routed request authenticated by an API key, and MUST finalize the
reservation from the upstream OpenAI-compatible `usage` payload when the
request completes.
The finalized input, output, cached-input, and cost values MUST update the same
API-key limit and usage-reporting paths used by subscription-backed requests.

#### Scenario: Source-routed response finalizes token usage

- **WHEN** an API key calls a source-routed model and the upstream response
  includes `usage.prompt_tokens=100` and `usage.completion_tokens=20`
- **THEN** the API-key reservation is finalized with 100 input tokens and 20
  output tokens
- **AND** `/v1/usage` for that key reflects the completed usage

#### Scenario: Missing usage fails closed for limited keys

- **GIVEN** an API key has a token or cost limit
- **WHEN** a source-routed response succeeds but lacks usable OpenAI `usage`
  fields
- **THEN** the system does not silently finalize zero usage
- **AND** the request fails or is marked failed according to the source-routing
  error contract
