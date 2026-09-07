## ADDED Requirements

### Requirement: Route recovery preserves standalone key authentication

Administrator route loading, error recovery, and unknown-route handling SHALL coexist with the standalone `/key-dashboard` route. Opening or recovering the key dashboard MUST NOT require an administrator session or load administrator-only data. Unknown administrator routes SHALL render the administrator not-found experience within the existing administrator authentication boundary.

#### Scenario: Key route remains accessible with administrator authentication required

- **GIVEN** the administrator dashboard requires a password and no administrator session exists
- **WHEN** the user opens `/key-dashboard`
- **THEN** the key entry screen renders without an administrator session request
- **AND** valid key authentication loads only that key's self-service data

#### Scenario: Unknown administrator route recovers without taking over the key route

- **GIVEN** the user is admitted to the administrator dashboard
- **WHEN** the user opens an unknown administrator path
- **THEN** a not-found view offers a path back to the dashboard
- **AND** subsequently opening `/key-dashboard` renders the standalone key route
