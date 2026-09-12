## ADDED Requirements
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
