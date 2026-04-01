## ADDED Requirements

### Requirement: Application pods can gate startup on Alembic head

When Kubernetes installation disables app-side startup migrations but still relies on a dedicated migration writer, the deployment MUST support an application startup gate that blocks the main app container until the live database reaches Alembic head.

#### Scenario: Bundled PostgreSQL install waits for schema head

- **WHEN** the chart installs with `postgresql.enabled=true`
- **AND** `database_migrate_on_startup=false`
- **AND** migration execution is delegated to the chart migration Job
- **THEN** application pods do not start the main container until the schema reaches Alembic head

#### Scenario: External Secrets install waits for schema head

- **WHEN** the chart installs with `externalSecrets.enabled=true`
- **AND** `database_migrate_on_startup=false`
- **THEN** application pods do not start the main container until the schema reaches Alembic head
