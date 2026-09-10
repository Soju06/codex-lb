## ADDED Requirements

### Requirement: Dashboard settings persist automatic quota failover

The database SHALL persist a non-null `dashboard_settings.quota_failover_enabled`
boolean with a true server default. Upgrade MUST enable existing rows, current
ORM metadata MUST match the migrated schema, and downgrade MUST remove only the
new setting column.

#### Scenario: Existing settings row defaults to enabled

- **GIVEN** a database is at the parent revision with an existing dashboard
  settings row
- **WHEN** the automatic quota failover revision is applied
- **THEN** `quota_failover_enabled` exists and is true for that row

#### Scenario: Downgrade and re-upgrade remain valid

- **WHEN** the database downgrades to the parent and upgrades to head again
- **THEN** the new column is removed and recreated without a divergent head
- **AND** schema drift detection reports no drift at head
