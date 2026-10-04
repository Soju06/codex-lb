## ADDED Requirements

### Requirement: Dispatch migration preserves the existing database

The dispatch-generation migration MUST add one nullable integer column without a default or backfill. Legacy rows MUST retain NULL generation; new aware rows MUST explicitly start at zero. Legacy ambiguous operations MUST fail closed for writes that require dispatch authority.

#### Scenario: An existing database is upgraded

- **WHEN** the additive migration runs on a database copy
- **THEN** existing rows and unrelated schema remain unchanged
- **AND** legacy rows retain NULL dispatch generation
- **AND** legacy in-flight operations cannot gain generation-aware write or redispatch authority
