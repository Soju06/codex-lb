# date-display-format Specification

## Purpose

Define the user-facing date display format preference and its effects on date/time rendering throughout the dashboard.

## Requirements

### Requirement: Date format preference is stored in localStorage

The system SHALL persist a date display format preference in localStorage under the key `codex-lb-date-display-format`. The valid values SHALL be `"default"` and `"iso8601"`. The default value SHALL be `"default"`.

#### Scenario: No stored preference

- **WHEN** the preference has never been saved
- **THEN** the system SHALL use `"default"` format

#### Scenario: User selects ISO 8601

- **WHEN** the user selects "ISO 8601" as the date format
- **THEN** the system SHALL persist `"iso8601"` to localStorage under `codex-lb-date-display-format`
- **AND** all applicable date display surfaces SHALL use ISO 8601 formatting

#### Scenario: User switches back to Default

- **WHEN** the user selects "Default" as the date format
- **THEN** the system SHALL persist `"default"` to localStorage
- **AND** all applicable date display surfaces SHALL revert to locale-dependent formatting

### Requirement: ISO 8601 format spec for date/time rendering

When the date display format is `"iso8601"`, the `formatTimeLong` function SHALL return `{ time: "YYYY-MM-DD", date: "HH:MM:SS" }` where:
- `time` is the date portion in ISO 8601 format (4-digit year, 2-digit month, 2-digit day, hyphen-separated)
- `date` is the time portion in 24-hour format (2-digit hour, 2-digit minute, 2-digit second, colon-separated)

#### Scenario: ISO 8601 rendering of a UTC timestamp

- **GIVEN** the date display format is `"iso8601"`
- **WHEN** formatting a timestamp corresponding to August 9, 2026 at 14:30:45 local time
- **THEN** `formatTimeLong` SHALL return `{ time: "2026-08-09", date: "14:30:45" }`

#### Scenario: Default rendering unchanged

- **GIVEN** the date display format is `"default"`
- **WHEN** formatting any timestamp
- **THEN** `formatTimeLong` SHALL return locale-dependent values as before (unchanged behavior)

### Requirement: Chart axes are not affected by date format

The date display format setting SHALL NOT affect Recharts x-axis tick formatting or data preparation. Charts (account trend, API trend, reports) SHALL continue to use their own x-axis formats regardless of the selected date display format.

#### Scenario: ISO 8601 setting does not change chart tooltips

- **GIVEN** the date display format is `"iso8601"`
- **WHEN** hovering over a point on any chart
- **THEN** the tooltip heading SHALL use `formatChartDateTime` as before (locale-dependent short month + day + time)

#### Scenario: Reports chart x-axis unchanged

- **GIVEN** the date display format is `"iso8601"`
- **WHEN** rendering a reports chart (tokens per day, cost per day, etc.)
- **THEN** the x-axis ticks SHALL remain `MM-DD` strings (e.g., `"08-09"`)
