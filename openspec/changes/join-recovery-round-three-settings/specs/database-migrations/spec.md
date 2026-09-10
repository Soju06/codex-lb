## ADDED Requirements

### Requirement: Recovery and round-three settings converge without data mutation

The migration graph SHALL have one head joining the recovery/request-budget
history and the dashboard conversation-archive history without changing rows
or schema in the merge revision.

#### Scenario: Upgrade from either populated parent

- **WHEN** a database at either parent is upgraded to the combined head
- **THEN** both histories SHALL be applied and existing rows SHALL be preserved

#### Scenario: Downgrade only the metadata merge

- **WHEN** the merge revision is downgraded to its direct parents
- **THEN** the merge SHALL restore parent version stamps without schema or row changes
