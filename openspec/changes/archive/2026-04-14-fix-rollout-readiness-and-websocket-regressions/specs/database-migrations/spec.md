## MODIFIED Requirements

### Requirement: Alembic as migration source of truth

The system SHALL use Alembic as the only runtime migration mechanism and SHALL NOT execute custom migration runners. When `database_migrate_on_startup=true`, application startup SHALL run Alembic upgrade to `head` and apply fail-fast behavior according to configuration. When `database_migrate_on_startup=false`, application startup SHALL NOT apply migrations itself, but it MUST verify that the database is already at Alembic `head` before continuing normal startup.

#### Scenario: Application startup performs Alembic migration

- **WHEN** the application starts with `database_migrate_on_startup=true`
- **THEN** it runs Alembic upgrade to `head`
- **AND** it applies fail-fast behavior according to configuration

#### Scenario: Startup migration is disabled but schema is current

- **GIVEN** the application starts with `database_migrate_on_startup=false`
- **AND** the database is already at Alembic `head`
- **WHEN** application startup continues
- **THEN** it skips local migration execution
- **AND** it proceeds with normal startup

#### Scenario: Startup migration is disabled while schema is behind

- **GIVEN** the application starts with `database_migrate_on_startup=false`
- **AND** the database is not yet at Alembic `head`
- **WHEN** application startup continues
- **THEN** it raises an explicit startup error instead of serving with a stale schema

## ADDED Requirements

### Requirement: Helm migrations use a single-writer execution path

The Helm deployment MUST use a dedicated migration Job as the only automatic schema writer for Kubernetes installs and upgrades. Application pods MUST NOT enable startup migrations implicitly because ExternalSecrets are enabled, and the Helm migration Job MUST run only after the chart's required ConfigMap and Secret references are creatable on install.

#### Scenario: Fresh install with chart-managed Secret

- **WHEN** a fresh Helm install creates chart-managed ConfigMap and Secret resources
- **THEN** the migration Job runs after those resources exist
- **AND** application pods do not auto-enable startup migrations just to bootstrap the schema

#### Scenario: Fresh install with ExternalSecrets

- **WHEN** `externalSecrets.enabled=true` on a fresh Helm install
- **THEN** the chart still uses the dedicated migration Job as the automatic schema writer
- **AND** application pods keep `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false` unless the operator explicitly enables it
