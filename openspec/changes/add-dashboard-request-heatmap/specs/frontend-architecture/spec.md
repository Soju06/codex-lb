## ADDED Requirements

### Requirement: Dashboard display mode preference

The dashboard display preference store SHALL expose a browser-local mode that selects between the Weekly credits pace card and the request activity heatmap. The default mode MUST preserve the current Weekly credits pace behavior.

#### Scenario: Restore dashboard display mode

- **WHEN** the application initializes dashboard preferences
- **THEN** it SHALL restore only valid stored mode values
- **AND** it SHALL fall back to Weekly credits pace for missing or invalid values
