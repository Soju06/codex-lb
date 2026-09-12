## ADDED Requirements
### Requirement: Verified PostgreSQL import
The local importer MUST read a consistent SQLite snapshot without modifying the source. It MUST require equal table and column sets, equal Alembic revision, valid source integrity and foreign keys, and an empty PostgreSQL target except for its revision. It MUST copy in one transaction, preserve encrypted bytes and all rows, verify canonical row checksums and counts before commit, and initialize generated sequences. Any validation failure MUST abort the copy.

#### Scenario: Occupied target
- **WHEN** the target contains application rows
- **THEN** the importer refuses and preserves them

### Requirement: PostgreSQL overlap gate
The configured PostgreSQL acceptance plan MUST require an approved candidate port, identical release migration and ORM definitions, and matching database revision before inference acceptance. Serving backends MUST disable startup migrations and use distinct instance identities and a shared encryption key.

#### Scenario: Compatible second backend
- **WHEN** an approved second backend uses the validated shared PostgreSQL deployment
- **THEN** it may pass database acceptance without the SQLite overlap prohibition

### Requirement: Consistent database cutover
An online SQLite snapshot MUST be treated as a rehearsal until all source writers have been stopped or fenced and the final snapshot has been imported and verified. Original data and routing MUST be preserved for recovery. Rollback after PostgreSQL writes MUST NOT silently switch to a stale SQLite copy.

#### Scenario: Writes after rehearsal
- **WHEN** the source changes after a rehearsal snapshot
- **THEN** final cutover requires a new consistent import
