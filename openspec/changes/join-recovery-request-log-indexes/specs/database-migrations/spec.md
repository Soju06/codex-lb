## ADDED Requirements

### Requirement: Recovery and request-log indexes converge

The migration graph MUST have a single head joining complete-transcript recovery
and request-log missing-cost indexes. The joining revision MUST change only
version stamps, preserving parent schemas, indexes and existing rows.

#### Scenario: Upgrade from either populated branch

- **WHEN** a database at either parent is upgraded to head
- **THEN** both migration histories are applied and existing account rows survive
- **AND** the resulting database has no schema drift

#### Scenario: Downgrade the join

- **WHEN** only the joining revision is downgraded
- **THEN** both parent stamps are restored without changing schema or rows
