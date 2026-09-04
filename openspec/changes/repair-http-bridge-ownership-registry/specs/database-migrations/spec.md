### Requirement: HTTP bridge ownership registry is bootstrapped after recovery repairs

When a database is stamped at or beyond the persisted HTTP bridge recovery
repair revision, the migration path MUST create the shared
`http_bridge_migration_object_ownership` table when it is absent, even if the
historical HTTP bridge operation index already exists.  The repair MUST be
idempotent and MUST NOT remove existing ownership markers or parent objects.

#### Scenario: Existing operation index but missing ownership table

- **GIVEN** `alembic_version` is at
  `20260901_000000_repair_persisted_schema_drift`
- **AND** `http_bridge_operations` and its recovery index exist
- **AND** `http_bridge_migration_object_ownership` does not exist
- **WHEN** startup migrations run to head
- **THEN** the ownership table exists before schema drift checking
- **AND** the application passes the startup schema drift guard
