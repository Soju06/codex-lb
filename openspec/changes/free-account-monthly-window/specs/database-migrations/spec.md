## ADDED Requirements

### Requirement: Legacy free-account usage-history rows are isolated from monthly semantics

The database migration path SHALL rename legacy free-account `usage_history.window` labels before normalized monthly-only free-account rows are written.

#### Scenario: Free-account legacy primary and secondary rows are renamed
- **WHEN** the migration runs against `usage_history` rows joined to accounts whose current `plan_type` is `free`
- **THEN** rows whose `window` is `primary` are rewritten to `old-primary`
- **AND** rows whose `window` is `secondary` are rewritten to `old-secondary`

#### Scenario: Non-free account rows remain unchanged
- **WHEN** the migration runs against `usage_history` rows joined to accounts whose current `plan_type` is not `free`
- **THEN** existing `primary` and `secondary` labels remain unchanged
