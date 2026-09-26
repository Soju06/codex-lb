## ADDED Requirements

### Requirement: Source catalogs expose aliases without upstream routing configuration

Both `/v1/models` and `/backend-api/codex/models` SHALL advertise a source model under its public `model` identity when `upstream_model` is configured. The server-only `upstream_model` metadata key MUST NOT be emitted in either catalog. Other capabilities, including base instructions and multi-agent version, MUST retain their existing behavior.

#### Scenario: Alias is advertised to Codex

- **GIVEN** public model `cd/gpt-6-astra` mapped to `cd/linxaq`
- **WHEN** a client lists models
- **THEN** the model ID or slug is `cd/gpt-6-astra`
- **AND** no `upstream_model` key is exposed
- **AND** declared multi-agent capabilities and base instructions remain available
