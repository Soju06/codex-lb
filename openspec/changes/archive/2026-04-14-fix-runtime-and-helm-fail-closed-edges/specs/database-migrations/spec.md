## ADDED Requirements

### Requirement: Chart-managed install migrations complete before application pods depend on them

For Helm installs that use chart-managed or already-existing database credentials directly available at render time, the migration Job MUST run before application pods start serving against the database schema. The chart MUST NOT rely on application pod startup to apply or race ahead of install-time schema creation in those paths.

#### Scenario: Fresh install with chart-managed database URL runs migration before app pods

- **WHEN** a fresh Helm install uses chart-managed PostgreSQL or a directly rendered external database URL
- **AND** startup migrations remain disabled in the application pods
- **THEN** the chart renders the migration Job as a pre-install hook
- **AND** application pods do not need to crash-loop waiting for schema creation
