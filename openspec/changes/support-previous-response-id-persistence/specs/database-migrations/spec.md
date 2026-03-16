## MODIFIED Requirements

### Requirement: Proxy durable state migrations remain additive and reproducible
Database migrations for proxy-managed durable state MUST create the tables and indexes required by new runtime capabilities without depending on mutable runtime configuration.

#### Scenario: response snapshot table is created on upgrade
- **WHEN** the application migrates a database that predates durable `previous_response_id` support
- **THEN** the migration creates a response snapshot table keyed by `response_id`
- **AND** the table includes indexed parent linkage required for recursive chain resolution after restart
