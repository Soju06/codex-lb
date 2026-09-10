## ADDED Requirements

### Requirement: Dashboard identity migration convergence

CPA catalog and dashboard identity migration histories SHALL converge through an append-only merge revision without changing published revisions. Upgrades SHALL preserve existing CPA source identities, encrypted source keys, catalog state, retention settings and guest generation. Existing role rows, grants and user identities and credentials SHALL remain intact after their identity parent has completed. Upgrades from older histories SHALL apply the existing identity migrations' documented legacy credential conversion and audit timestamp normalization. Audit history and existing actor/target attribution SHALL remain intact.

#### Scenario: Populated independent histories

- **WHEN** a database starts at the published CPA/guest head, the dashboard identity head, or both parent stamps
- **THEN** upgrading to head SHALL produce one head with both schemas and their preserved data

#### Scenario: Merge-only downgrade

- **WHEN** the merge is downgraded to either immediate parent
- **THEN** both parent stamps and both schemas SHALL remain without deleting or changing stored data
- **AND** re-upgrading SHALL restore the single merge stamp without schema drift
