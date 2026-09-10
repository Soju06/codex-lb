## ADDED Requirements

### Requirement: Rejection evidence schema bootstrap
Schema bootstrap SHALL add missing rejection-generation and probe-claim columns without overwriting existing values or failing when those columns already exist.

#### Scenario: Bootstrap an existing rejection schema
- **GIVEN** an unversioned or legacy-stamped account schema with some or all rejection-generation and probe-claim columns
- **WHEN** startup upgrades the schema
- **THEN** the upgrade SHALL complete with all required columns
- **AND** existing rejection evidence SHALL remain unchanged
