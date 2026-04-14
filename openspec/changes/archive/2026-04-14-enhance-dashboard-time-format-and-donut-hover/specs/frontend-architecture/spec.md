## MODIFIED Requirements

### Requirement: Dashboard page

The Dashboard page SHALL display summary metric cards, primary and secondary usage donut charts with legends, account status cards grid, and a recent requests table with filtering and pagination. Donut charts MUST coordinate hover state between slices and legend rows.

#### Scenario: Donut legend hover highlights the matching slice

- **WHEN** a user hovers a donut legend row, including its color dot or text label
- **THEN** the matching donut slice enlarges using the chart library's active-slice mechanism
- **AND** the hovered legend row shows a visible outline tied to the same series color

#### Scenario: Donut slice hover highlights the matching legend row

- **WHEN** a user hovers a donut slice
- **THEN** the matching legend row shows the same active outline treatment
- **AND** the chart canvas leaves enough room that the enlarged slice does not clip against the card container

### Requirement: Settings page

The Settings page SHALL include sections for: appearance settings (theme and datetime time format), routing settings (sticky threads, reset priority, prompt-cache affinity TTL), password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, API key management (table, create, edit, delete, regenerate), and sticky-session administration.

#### Scenario: Appearance time format defaults to 12h

- **WHEN** a user opens the dashboard without a previously saved time-format preference
- **THEN** the Appearance section shows `12h` as the selected time format
- **AND** datetime labels across the dashboard render in 12-hour format

#### Scenario: Appearance time format updates datetime rendering

- **WHEN** a user changes the Appearance time format from `12h` to `24h` or from `24h` to `12h`
- **THEN** the preference is persisted locally for the dashboard UI
- **AND** datetime labels and chart tooltip timestamps across the application update to use the selected hour format
