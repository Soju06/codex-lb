## MODIFIED Requirements

### Requirement: Alembic as migration source of truth

The system SHALL use Alembic as the only runtime migration mechanism and SHALL NOT execute custom migration runners. Dashboard settings schema changes, including weekly pace working days, MUST be represented by Alembic revisions and ORM metadata so startup drift detection can verify them.

The security-work migration lineage MUST reconcile databases that previously
ran the broader security-work live stack by preserving the legacy
`quota_planner_settings.auto_redeem_expiring_reset_credits` and
`quota_planner_settings.reset_credit_redeem_lead_minutes` columns in ORM
metadata and adding them idempotently when the `quota_planner_settings` table
exists without those columns.

#### Scenario: Application startup performs Alembic migration

- **WHEN** the application starts
- **THEN** it runs Alembic upgrade to `head`
- **AND** it applies fail-fast behavior according to configuration

#### Scenario: Dashboard settings migration persists weekly pace working days

- **WHEN** migrations run to head on an existing install
- **THEN** `dashboard_settings` contains a non-null `weekly_pace_working_days` column
- **AND** existing rows default to `0,1,2,3,4,5,6`

#### Scenario: Security-work live lineage quota settings remain schema-compatible

- **GIVEN** a database has already run the broader security-work live-stack lineage and contains `quota_planner_settings`
- **WHEN** security-work migrations upgrade to head
- **THEN** `quota_planner_settings.auto_redeem_expiring_reset_credits` exists as a non-null boolean column
- **AND** `quota_planner_settings.reset_credit_redeem_lead_minutes` exists as a non-null integer column
- **AND** post-migration schema drift detection reports no missing ORM columns for those fields
