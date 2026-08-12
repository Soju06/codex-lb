## ADDED Requirements

### Requirement: Appearance settings include date format toggle

The Appearance settings section SHALL include a "Date format" toggle row with two options: "Default" and "ISO 8601". The toggle SHALL be placed between the Time format and Account rows settings. Selecting an option SHALL immediately apply the new format to applicable read-only date/time presentation text across the dashboard.

#### Scenario: Default date format is selected initially

- **WHEN** a user opens the Appearance settings section with no prior date format preference
- **THEN** the "Default" option SHALL be selected (aria-pressed true)
- **AND** applicable read-only date/time presentation text SHALL render using locale-dependent formatting

#### Scenario: Switching to ISO 8601

- **WHEN** the user clicks the "ISO 8601" option in the Date format row
- **THEN** the "ISO 8601" option SHALL be selected
- **AND** request log and conversation table cells SHALL display date on the top line in `YYYY-MM-DD` format and time on the bottom line in `HH:MM:SS` format
- **AND** the preference persists across page reloads

### Requirement: Accounts and API trend chart x-axis uses MM-DD format

The x-axis tick format of the Account Trend and API Trend charts SHALL be `MM-DD` (month and day extracted from the ISO timestamp data key), matching the reports chart convention. This format SHALL be locale-independent.

#### Scenario: Account trend chart x-axis ticks

- **WHEN** the Account Trend chart renders with timestamp data
- **THEN** the x-axis tick labels SHALL be in `MM-DD` format (e.g., `"08-09"`)

#### Scenario: API trend chart x-axis ticks

- **WHEN** the API Trend chart renders with timestamp data
- **THEN** the x-axis tick labels SHALL be in `MM-DD` format (e.g., `"08-09"`)
