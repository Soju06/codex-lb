## ADDED Requirements

### Requirement: Source-model base instructions are preserved in Codex catalogs

For enabled OpenAI-compatible source models eligible for Codex catalogs,
`GET /backend-api/codex/models` and `GET /v1/models` with `client_version` MUST emit a string-valued
`raw_metadata_json.base_instructions` unchanged in the catalog's
`base_instructions` field. Missing or non-string values MUST emit the existing
empty-string default. This projection MUST preserve the existing passthrough
of client capability metadata and MUST NOT imply support for additional tools.

#### Scenario: Source instructions reach both Codex catalog routes

- **GIVEN** an enabled Responses-capable source model declares string base instructions
- **WHEN** a client requests either Codex catalog route
- **THEN** the entry contains the exact instructions, including whitespace and Unicode
- **AND** existing `tool_mode` and `multi_agent_version` metadata remain unchanged

#### Scenario: Missing or malformed instructions retain the empty default

- **GIVEN** an eligible source model omits base instructions or supplies a non-string value
- **WHEN** a client requests its Codex catalog entry
- **THEN** the entry contains `base_instructions` with the empty string

#### Scenario: An explicitly empty instruction string stays empty

- **GIVEN** an eligible source model declares an empty instruction string
- **WHEN** a client requests its Codex catalog entry
- **THEN** the entry contains the empty string without substituting bundled instructions
