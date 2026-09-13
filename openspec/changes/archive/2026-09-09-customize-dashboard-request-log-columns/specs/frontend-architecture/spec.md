## ADDED Requirements

### Requirement: Configurable dashboard request-log columns

The dashboard SHALL let operators show or hide request-log columns and MUST
preserve at least one visible column. Column choices MUST be stored locally per
browser and restored on later visits. Malformed or stale stored choices MUST
fall back to supported defaults without preventing the dashboard from
rendering. A restore-default action MUST clear customized visibility and width
values.

#### Scenario: Choose visible request-log columns

- **WHEN** an operator selects or deselects columns in the dashboard request-log column chooser
- **THEN** the corresponding request-log headers and cells are shown or hidden
- **AND** the choice is restored when that browser revisits the dashboard

#### Scenario: Preserve a usable table

- **WHEN** only one request-log column remains visible
- **THEN** the dashboard prevents that final column from being hidden

#### Scenario: Recover from invalid stored preferences

- **WHEN** stored request-log preferences are malformed or contain unsupported column identifiers
- **THEN** the dashboard renders with supported default columns and widths

### Requirement: Resizable dashboard request-log columns

The dashboard SHALL render a vertical resize separator at the trailing edge of
each visible request-log header. Dragging a separator MUST adjust that column
within bounded minimum and maximum widths without changing other configured
columns. Individual widths MUST be stored locally per browser and restored on
later visits. Separators MUST support keyboard adjustment, and the table's
minimum width MUST be derived from its visible column widths so overflow
remains horizontally scrollable without a global table-width control.

#### Scenario: Resize a request-log column by dragging

- **WHEN** an operator drags a request-log header separator horizontally
- **THEN** the corresponding header and body column change width
- **AND** the selected width is restored on a later dashboard visit in the same browser

#### Scenario: Resize a request-log column with the keyboard

- **WHEN** a focused request-log header separator receives a Left or Right arrow key
- **THEN** the corresponding column width decreases or increases by the documented step within its bounds

#### Scenario: Wide columns remain reachable

- **WHEN** the sum of visible request-log column widths exceeds the available viewport
- **THEN** the table remains horizontally scrollable
- **AND** no separate global table-width control is displayed
