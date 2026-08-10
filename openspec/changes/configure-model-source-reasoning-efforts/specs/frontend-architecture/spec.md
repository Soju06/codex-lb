## ADDED Requirements

### Requirement: Model Source reasoning configuration is editable

The Model Source create and edit forms MUST expose supported reasoning-effort
controls when the Reasoning capability is enabled. The form MUST allow an
operator to select one or more supported efforts, select one configured effort
as the default, and persist the result in the model's existing raw metadata.
The form MUST preserve both string and descriptive object representations when
prefilling an existing source, and MUST prevent saving a reasoning model with
no selected effort. Disabling Reasoning MUST remove the reasoning metadata
managed by this form while preserving unrelated raw metadata.

#### Scenario: Configure reasoning efforts for a new Model Source

- **WHEN** an operator enables Reasoning in the Model Source form
- **THEN** the form displays supported-effort controls and a default-effort
  selector
- **AND** the operator can select multiple efforts
- **AND** the default selector contains only selected efforts

#### Scenario: Edit saved reasoning metadata

- **GIVEN** an existing Model Source stores string or descriptive reasoning
  levels in raw metadata
- **WHEN** an operator opens the edit form
- **THEN** the saved efforts and valid default are prefilled
- **AND** saving a reasoning change persists the selected levels and default

#### Scenario: Disable reasoning without dropping unrelated metadata

- **GIVEN** a Model Source has reasoning metadata and unrelated raw metadata
- **WHEN** an operator disables Reasoning and saves
- **THEN** the managed reasoning metadata is removed
- **AND** unrelated raw metadata remains unchanged
