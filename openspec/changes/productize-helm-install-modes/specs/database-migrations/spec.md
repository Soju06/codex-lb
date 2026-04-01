## ADDED Requirements

### Requirement: Application pods can gate startup on Alembic head

When Kubernetes installation disables app-side startup migrations but still relies on a dedicated migration writer, the deployment MUST support an application startup gate that blocks the main app container until the live database reaches Alembic head.

#### Scenario: Bundled PostgreSQL waits for database connectivity before startup migration

- **WHEN** the chart installs with `postgresql.enabled=true`
- **AND** the self-contained bundled mode enables startup migrations in the app container
- **THEN** application pods wait for database connectivity before starting the main container
- **AND** the main container can bootstrap schema on fresh install without racing a missing database endpoint

#### Scenario: External Secrets install waits for schema head

- **WHEN** the chart installs with `externalSecrets.enabled=true`
- **AND** `database_migrate_on_startup=false`
- **THEN** application pods do not start the main container until the schema reaches Alembic head
