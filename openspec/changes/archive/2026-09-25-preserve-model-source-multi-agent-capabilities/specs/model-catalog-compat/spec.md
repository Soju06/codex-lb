# model-catalog-compat Delta

## ADDED Requirements

### Requirement: Model-source Codex capability metadata is preserved

When an enabled OpenAI-compatible model source declares valid Codex catalog fields in its model metadata, `GET /backend-api/codex/models` and the `client_version` variant of `GET /v1/models` MUST preserve `tool_mode`, `multi_agent_version`, `multi_agent_reasoning_effort`, `supports_experimental_context`, `supports_reasoning_effort_updates`, `use_responses_lite`, and `experimental_supported_tools`. A string `base_instructions` value MUST be served in the catalog's `base_instructions` field. Missing fields MUST retain the existing conservative defaults.

#### Scenario: Multi-agent source metadata reaches Codex clients

- **GIVEN** a source model declares `tool_mode=code_mode_only`,
  `multi_agent_version=v2`, and base instructions
- **WHEN** a Codex client requests the source model catalog
- **THEN** the model entry contains those values unchanged

#### Scenario: Source without metadata remains conservative

- **GIVEN** a source model has no multi-agent metadata
- **WHEN** its catalog entry is generated
- **THEN** it does not advertise a multi-agent version or collaboration
  namespace capability
