## ADDED Requirements

### Requirement: Dashboard invitation migration convergence

CPA catalog and dashboard invitation histories SHALL converge through an append-only merge without changing published revisions. Existing invitations, token hashes, user and creator identities, expiry and consumption/revocation state SHALL remain intact, together with source keys/catalogs, roles/grants, users/credentials, API key ownership and audit history.

#### Scenario: Populated independent parents

- **WHEN** a database at either parent or both stamps upgrades to head
- **THEN** the database SHALL contain both schemas at one head without changing previously applied data
- **AND** a database without invitations SHALL gain an empty invitation table

#### Scenario: Merge-only downgrade and re-upgrade

- **WHEN** the merge is downgraded to either immediate parent and upgraded again
- **THEN** downgrade SHALL restore both parent stamps without dropping schemas or data
- **AND** re-upgrade SHALL restore one head without schema drift
