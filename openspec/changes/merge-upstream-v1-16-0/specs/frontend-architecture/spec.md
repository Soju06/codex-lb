## ADDED Requirements

### Requirement: Frontend contracts match merged backend dashboard behavior

The frontend MUST adopt upstream dashboard changes for account sorting, compact quota display, request-log filtering, dashboard session lifetime settings, and API-key/account schemas while preserving local fields and behavior that remain present in the merged backend.

#### Scenario: Dashboard uses merged schema fields

- **WHEN** the dashboard fetches accounts, request logs, settings, and API-key data
- **THEN** frontend schemas accept the merged backend payloads
- **AND** local-only response fields that still exist after the merge are not dropped from typed contracts or mocks

#### Scenario: Request-log filters include API-key filtering

- **WHEN** a user filters dashboard request logs by API key after the merge
- **THEN** the frontend sends the merged filter parameter expected by the backend
- **AND** existing filters continue to work

#### Scenario: Session lifetime setting is editable

- **WHEN** the merged backend exposes dashboard session lifetime settings
- **THEN** the settings UI renders and persists the value
- **AND** existing TOTP, password, routing, and appearance settings remain accessible
