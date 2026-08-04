## ADDED Requirements

### Requirement: Disabled request-log pruning is explained and presets are non-destructive

When the effective request-log retention value is `0`, the Settings data retention card SHALL show neutral informational text that request-log pruning is disabled, logs are retained indefinitely, and storage will grow over time, and SHALL offer 30-day and 90-day request-log retention presets. The informational text MUST NOT characterize disabled pruning as unsafe or direct the operator to change it. Activating a preset MUST update only the local request-log retention form value and MUST NOT persist any setting until the operator activates the existing explicit save action. Rendering the information and presets MUST NOT change the stored override or any other retention policy.

#### Scenario: Effective disabled state shows information and presets

- **GIVEN** effective request-log retention is `0`
- **WHEN** an operator views the data retention card
- **THEN** the card explains neutrally that request-log pruning is disabled and
  logs are retained indefinitely
- **AND** the text notes that storage will grow over time without directing the
  operator to change the policy
- **AND** the card offers 30-day and 90-day request-log retention presets
- **AND** no settings update is submitted

#### Scenario: Preset selection requires explicit save

- **GIVEN** effective request-log retention is `0`
- **WHEN** an operator activates the 30-day or 90-day preset
- **THEN** the request-log retention form value changes to the selected number
- **AND** no settings update is submitted until the operator activates save
- **AND** usage-history retention remains unchanged

#### Scenario: Enabled effective policy does not show disabled-state information

- **GIVEN** effective request-log retention is greater than `0`
- **WHEN** an operator views the data retention card
- **THEN** the disabled-state information and presets are not shown
