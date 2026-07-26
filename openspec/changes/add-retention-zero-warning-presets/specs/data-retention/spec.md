## ADDED Requirements

### Requirement: Disabled request-log retention is actionable but non-destructive

When the effective request-log retention value is `0`, the Settings data retention card SHALL show a visible operator warning that request-log pruning is disabled and SHALL offer 30-day and 90-day request-log retention presets. Activating a preset MUST update only the local request-log retention form value and MUST NOT persist any setting until the operator activates the existing explicit save action. Rendering the warning and presets MUST NOT change the stored override or any other retention policy.

#### Scenario: Effective disabled state shows warning and presets

- **GIVEN** effective request-log retention is `0`
- **WHEN** an operator views the data retention card
- **THEN** the card shows a warning that request-log pruning is disabled
- **AND** the card offers 30-day and 90-day request-log retention presets
- **AND** no settings update is submitted

#### Scenario: Preset selection requires explicit save

- **GIVEN** effective request-log retention is `0`
- **WHEN** an operator activates the 30-day or 90-day preset
- **THEN** the request-log retention form value changes to the selected number
- **AND** no settings update is submitted until the operator activates save
- **AND** usage-history retention remains unchanged

#### Scenario: Enabled effective policy does not show the disabled warning

- **GIVEN** effective request-log retention is greater than `0`
- **WHEN** an operator views the data retention card
- **THEN** the disabled-state warning and presets are not shown
