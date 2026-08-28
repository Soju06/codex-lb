## MODIFIED Requirements

### Requirement: Model-source reasoning metadata editor

The dashboard MUST let operators configure the reasoning metadata stored on
model-source models without assuming one global effort vocabulary.

#### Scenario: Edit arbitrary supported reasoning efforts

- **GIVEN** a model source whose `raw_metadata_json` contains
  `supports_reasoning: true`
- **AND** its `supported_reasoning_levels` include values such as `none` or a
  provider-specific slug
- **WHEN** the dashboard opens the model-source create or edit form
- **THEN** the reasoning controls MUST show those effort slugs without dropping
  or rewriting them
- **AND** saving the form MUST write the edited effort list back into
  `supported_reasoning_levels`.

#### Scenario: Normalize stale defaults during save

- **GIVEN** a model source whose configured default effort is no longer present
  in the edited supported-effort list
- **WHEN** the operator saves the form
- **THEN** the dashboard MUST replace the stale default with one of the
  configured supported efforts
- **AND** it MUST NOT leave `default_reasoning_level` pointing at a removed
  value.

#### Scenario: Seed a first-time reasoning configuration

- **GIVEN** an operator enables reasoning for a model source that previously had
  no configured supported-effort list
- **WHEN** the dashboard reveals the reasoning metadata controls
- **THEN** the form MUST seed an editable default effort list and default value
  so the operator can save a valid initial configuration
- **AND** the operator MUST still be able to replace that seed with arbitrary
  effort slugs before saving.
