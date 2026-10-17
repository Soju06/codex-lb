## ADDED Requirements

### Requirement: Working-hour updates contain valid clock times

The quota planner settings API MUST reject supplied working-hour times unless
they use ASCII `HH:MM` format with hours from 00 through 23 and minutes from
00 through 59. An invalid update MUST NOT persist any accompanying changes.
Omitted or null times MUST retain their existing values. Reading legacy
settings MUST remain possible, and the existing scheduler fallback for legacy
malformed times MUST remain available.

#### Scenario: Impossible clock time does not change settings

- **WHEN** an operator supplies `99:99`, `24:00`, or `12:60` with another setting
- **THEN** the API returns a validation error and no setting changes

#### Scenario: Boundary clock times are accepted

- **WHEN** an operator supplies `00:00` and `23:59`
- **THEN** both values are saved and returned unchanged
