## ADDED Requirements

### Requirement: Reset pooling composes with dashboard invitations

The database MUST upgrade to one head from either the published reset/user merge or dashboard invitation revision. Existing invitation hashes, lifecycle timestamps, flags and inviter snapshots MUST remain unchanged, together with users, roles, grants, session generations, audit data, reset policy and redemption bindings. Merge-only downgrade and re-upgrade MUST preserve both parent schemas and their data. Existing user-management, authorization and CSRF behavior MUST remain unchanged.

#### Scenario: Populated invitation data survives composition

- **WHEN** a database with pending, consumed or revoked invitation rows upgrades and rolls back only the merge
- **THEN** every invitation column and its associated user state remains unchanged
- **AND** both direct parent revisions remain stamped

#### Scenario: Reset data adopts invitations

- **WHEN** a reset database upgrades to the composition head
- **THEN** the invitation table is created empty without changing reset bindings or authorization
