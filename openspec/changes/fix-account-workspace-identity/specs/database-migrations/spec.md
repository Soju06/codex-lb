## MODIFIED Requirements

### Requirement: Account schema preserves workspace membership metadata

The database schema SHALL store optional workspace and seat metadata for accounts without rewriting existing account primary keys.

#### Scenario: Existing account ids remain stable

- **WHEN** the workspace identity migration is applied
- **THEN** existing `accounts.id` values are not modified
- **AND** nullable `workspace_id`, `workspace_label`, and `seat_type` columns are available
