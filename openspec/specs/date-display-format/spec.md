# date-display-format Specification

## Purpose
Operator-selectable dashboard date/time rendering (localStorage preference, ISO 8601 contract) without disturbing chart axis formats.
## Requirements
### Requirement: Date format preference is stored in localStorage

The system SHALL persist a date display format preference in localStorage under the key `codex-lb-date-display-format`. The valid values SHALL be `"default"` and `"iso8601"`. The default value SHALL be `"default"`. The preference SHALL apply only to read-only date/time presentation text.

#### Scenario: No stored preference

- **WHEN** the preference has never been saved
- **THEN** the system SHALL use `"default"` format

#### Scenario: User selects ISO 8601

- **WHEN** the user selects "ISO 8601" as the date format
- **THEN** the system SHALL persist `"iso8601"` to localStorage under `codex-lb-date-display-format`
- **AND** all applicable read-only date/time presentation text SHALL use ISO 8601 formatting

#### Scenario: User switches back to Default

- **WHEN** the user selects "Default" as the date format
- **THEN** the system SHALL persist `"default"` to localStorage
- **AND** all applicable read-only date/time presentation text SHALL revert to locale-dependent formatting

#### Scenario: Interactive date and time controls retain their own format

- **GIVEN** a date or time is shown within an interactive control used to enter, edit, select, or filter a value
- **WHEN** the user switches between "Default" and "ISO 8601"
- **THEN** inputs, calendars, date pickers, selectors, and equivalent interactive controls SHALL retain the format provided by their component or browser
- **AND** the preference SHALL NOT change the control's value representation or interaction behavior

#### Scenario: Verbatim API and data representations remain unchanged

- **GIVEN** a date or timestamp appears inside a verbatim API or data representation
- **WHEN** the user switches between "Default" and "ISO 8601"
- **THEN** raw JSON, request and response payloads, metadata, copied values, filenames, downloads, and exports SHALL preserve their source representation
- **AND** the preference SHALL NOT rewrite those values

#### Scenario: Read-only presentation text follows the selected format

- **GIVEN** a date or timestamp is presented as non-interactive text in a table cell, detail field, status, or informational label
- **WHEN** the user switches between "Default" and "ISO 8601"
- **THEN** the rendered text SHALL update immediately to the selected format

#### Scenario: Daily report table follows the selected format

- **GIVEN** the daily report breakdown table is mounted
- **WHEN** the user switches between "Default" and "ISO 8601"
- **THEN** the table's Day column SHALL update immediately
- **AND** Default SHALL use locale-dependent formatting
- **AND** ISO 8601 SHALL use `YYYY-MM-DD` formatting

#### Scenario: Quota planner decision peak follows the selected format

- **GIVEN** a quota planner decision presents `target_peak_at` as a read-only Peak label
- **WHEN** the user switches between "Default" and "ISO 8601"
- **THEN** the Peak label SHALL update immediately to the selected format
- **AND** the underlying decision details value SHALL remain unchanged

### Requirement: ISO 8601 format spec for date/time rendering

When the date display format is `"iso8601"`, the `formatTimeLong` function SHALL return `{ time: "HH:MM:SS", date: "YYYY-MM-DD" }` for read-only presentation text where:
- `time` is always the clock-time portion in 24-hour format (2-digit hour, 2-digit minute, 2-digit second, colon-separated)
- `date` is always the calendar-date portion in ISO 8601 format (4-digit year, 2-digit month, 2-digit day, hyphen-separated)
The semantic meaning of these fields SHALL remain stable across date display formats. Rendered date/time surfaces that display ISO values SHALL order the `date` value before the `time` value.

#### Scenario: ISO 8601 rendering of a UTC timestamp

- **GIVEN** the date display format is `"iso8601"`
- **WHEN** formatting a timestamp corresponding to August 9, 2026 at 14:30:45 local time
- **THEN** `formatTimeLong` SHALL return `{ time: "14:30:45", date: "2026-08-09" }`

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

