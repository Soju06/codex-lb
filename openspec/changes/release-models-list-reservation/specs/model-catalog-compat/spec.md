## ADDED Requirements

### Requirement: Model catalog reservations are released on every exit path

The model catalog builders for `GET /v1/models` and `GET /backend-api/codex/models` SHALL release the API-key usage reservation after acquisition on normal return, exception, or cancellation. The builders MUST preserve the existing reservation amount and successful response shape.

#### Scenario: OpenAI-compatible catalog lookup fails

- **WHEN** `_list_enabled_source_catalog_models` raises after reservation
  acquisition while serving `GET /v1/models`
- **THEN** the reservation row is released
- **AND** its reserved usage is no longer charged to the key

#### Scenario: Codex-native catalog lookup fails

- **WHEN** `_list_enabled_source_catalog_models` raises after reservation
  acquisition while serving `GET /backend-api/codex/models`
- **THEN** the reservation row is released
- **AND** its reserved usage is no longer charged to the key

#### Scenario: Catalog request is cancelled

- **WHEN** either model catalog builder is cancelled after reservation
  acquisition
- **THEN** the reservation is released before cancellation propagates
