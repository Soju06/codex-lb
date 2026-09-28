## MODIFIED Requirements

### Requirement: Dashboard limit warm-up controls

The dashboard SHALL expose global limit warm-up controls in Settings and
per-account opt-in/status in account views. The global default SHALL be
disabled. Settings SHALL include an exhausted-threshold percent control that
determines which pre-reset usage samples count as eligible for reset-confirmed
warm-up. The control SHALL accept values from `0` through `100`, inclusive, and
the default SHALL be `0`, meaning every confirmed reset is eligible.

#### Scenario: Configure warm-up behavior

- **WHEN** an operator opens Settings
- **THEN** the dashboard shows controls for enabling limit warm-up, selecting
  primary/secondary/both windows, setting the warm-up model, setting the
  prompt, setting the exhausted threshold, and setting the cooldown
- **AND** the exhausted-threshold control accepts `0%`
- **AND** the UI explains that `0%` qualifies every confirmed reset

#### Scenario: Validate the zero threshold before save

- **WHEN** an operator enters `0` in the exhausted-threshold field
- **THEN** frontend validation accepts the value and the settings payload can
  be saved

#### Scenario: Validate warm-up settings before save

- **WHEN** an operator edits warm-up model, prompt, exhausted threshold, or
  cooldown fields
- **THEN** the dashboard enforces the same non-empty, max-length, percent, and
  integer cooldown bounds as the backend API before enabling save

#### Scenario: Show per-account opt-in and last attempt

- **WHEN** account summaries include limit warm-up status
- **THEN** the dashboard shows whether warm-up is enabled for that account
- **AND** it shows the latest attempt window, status, model, and
  completion/attempt time when available

#### Scenario: Warm-up controls are accessible by name

- **WHEN** an operator navigates the dashboard with assistive technology
- **THEN** global and per-account warm-up toggles expose descriptive accessible
  names that identify the setting and account context

#### Scenario: Validate positive threshold behavior before save

- **WHEN** an operator enters a value greater than `0` and no greater than `100`
  in the exhausted-threshold field
- **THEN** frontend validation accepts the value and the settings payload can
  be saved
