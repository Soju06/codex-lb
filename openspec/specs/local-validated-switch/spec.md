# Local validated switch

## Purpose
Preserve local connections while admitting verified candidate backends.

## Requirements
### Requirement: Validated connection switching
The local entrance MUST retain the current destination when candidate readiness, catalog, or the configured acceptance command fails. The command MUST run with a deadline and the candidate URL. Control operations MUST be serialized and restricted to the current OS user through a private Unix socket. Successful switching MUST persist the new destination before publishing it to new connections.

#### Scenario: Failed acceptance
- **WHEN** the candidate acceptance command fails or times out
- **THEN** the previous destination remains active

### Requirement: Established connection continuity
The entrance MUST pin each accepted TCP connection to one backend and report active connection counts. Switching and rollback MUST NOT restart or stop any backend. Operators MUST NOT stop a backend until its connections have drained. Automated switching MUST NOT perform database migrations.

#### Scenario: Stream crosses switch
- **WHEN** an established stream is active while the destination switches
- **THEN** its bytes continue through the original backend and new connections use the new backend

### Requirement: Explicit disruptive maintenance
The legacy LaunchAgent stop/start CLI MUST refuse execution unless the operator explicitly enables disruptive maintenance.

#### Scenario: Default invocation
- **WHEN** the legacy CLI is invoked without the maintenance flag
- **THEN** it fails before stopping the current service

### Requirement: Conservative SQLite compatibility gate
When a local database plan is configured, acceptance MUST reject a destination absent from that plan. Before inference probes, the validator MUST require an existing SQLite database, a single current revision matching both release heads, identical migration file contents and identical ORM definitions. It MUST open the database read-only and MUST NOT migrate or stamp it. This gate MUST NOT be described as proof of application-level concurrency correctness.

#### Scenario: Revision mismatch
- **WHEN** the candidate migration head differs from the active database revision
- **THEN** acceptance fails and neither the database nor active destination changes

#### Scenario: Matching revision with changed migration
- **WHEN** releases declare the same head but contain different migration code
- **THEN** acceptance fails

### Requirement: SQLite overlap prohibition
The configured SQLite switch plan MUST reject any destination other than the active port, even when release schemas match, because the application holds an exclusive SQLite lifetime lock. Schema matching alone MUST NOT authorize an overlapping backend.

#### Scenario: Matching schema on a second port
- **WHEN** an operator requests a second backend using the same SQLite deployment
- **THEN** acceptance fails with a maintenance or database migration requirement

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
