## ADDED Requirements

### Requirement: Subagent prompt-cache TTL appears in Routing Settings

The Routing Settings section SHALL include a persisted integer control for the subagent prompt-cache TTL. The control SHALL default to empty (No Cache). The control follows the same persistence pattern as the existing prompt-cache affinity TTL field.

#### Scenario: Save subagent prompt-cache TTL

- **WHEN** a user enters a non-negative integer value for the subagent prompt-cache TTL in the Routing Settings section
- **AND** clicks save
- **THEN** the app calls `PUT /api/settings` with the updated TTL
- **AND** the settings response reflects the saved value
- **AND** subsequent requests carrying `x-parent-session-id` use the new TTL

#### Scenario: Empty or 0 means No Cache

- **WHEN** a user leaves the subagent TTL field empty or enters 0
- **AND** clicks save
- **THEN** the app calls `PUT /api/settings` with `null`
- **AND** subagent bridge sessions and stream leases are released immediately after stream end
