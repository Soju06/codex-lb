## ADDED Requirements

### Requirement: OpenAI-compatible Model Sources publish configured reasoning efforts

When an enabled OpenAI-compatible Model Source model has raw metadata with
`supports_reasoning: true`, the OpenAI-compatible model catalog MUST publish
the configured `supported_reasoning_levels` as the model's supported reasoning
levels and MUST publish a valid `default_reasoning_level`. String entries and
descriptive objects with an `effort` string MUST both be accepted. Empty,
duplicate, and `none` entries MUST NOT be advertised. When the configured
default is absent or is not one of the published efforts, the first published
effort MUST be used as the default. If reasoning is not enabled or no valid
efforts are configured, the model MUST publish no supported reasoning levels
and no default reasoning effort.

#### Scenario: String reasoning levels are published

- **GIVEN** a Model Source model has `supports_reasoning: true`
- **AND** its metadata contains `supported_reasoning_levels` of
  `["low", "medium", "high"]`
- **AND** its metadata contains `default_reasoning_level: "high"`
- **WHEN** the OpenAI-compatible model catalog is built
- **THEN** the model publishes `low`, `medium`, and `high`
- **AND** its default reasoning effort is `high`

#### Scenario: Descriptive reasoning levels are published

- **GIVEN** a Model Source model has `supports_reasoning: true`
- **AND** its metadata contains descriptive levels with `effort` values
  `minimal` and `xhigh`
- **WHEN** the OpenAI-compatible model catalog is built
- **THEN** both efforts are published
- **AND** their descriptions are preserved

#### Scenario: Invalid reasoning levels are filtered

- **GIVEN** a Model Source model has reasoning metadata containing duplicate,
  empty, and `none` entries
- **WHEN** the OpenAI-compatible model catalog is built
- **THEN** only valid unique efforts are published
- **AND** `none` is not published

#### Scenario: Reasoning metadata is ignored when disabled

- **GIVEN** a Model Source model has configured effort metadata
- **AND** `supports_reasoning` is not `true`
- **WHEN** the OpenAI-compatible model catalog is built
- **THEN** the model publishes no supported reasoning levels
- **AND** it publishes no default reasoning effort
