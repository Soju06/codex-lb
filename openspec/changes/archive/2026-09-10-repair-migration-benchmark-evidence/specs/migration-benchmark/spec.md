## MODIFIED Requirements

### Requirement: Explicit disposable benchmark invocation

The developer benchmark MUST read its PostgreSQL URL from a nonempty environment variable explicitly selected with `--db-url-env NAME`, without placing the URL in process arguments. It MUST fail before database access if the selected variable is missing or empty. The developer benchmark MUST require an explicit PostgreSQL target, disposable ownership acknowledgement, exact base and target Alembic revision IDs, fixture counts, seed and output directory. It MUST reject a nonempty database without resetting it, MUST set both database environment variables to the supplied target before app imports, and MUST NOT run during application startup or as a mandatory CI check.

#### Scenario: Existing data is protected
- **WHEN** the acknowledged target already contains user tables
- **THEN** the command MUST fail without migrating, deleting or seeding rows

#### Scenario: Protected explicit target input
- **WHEN** the operator selects a populated URL environment variable and acknowledges disposable ownership
- **THEN** the command MUST use that target without requiring URL credentials in its arguments

### Requirement: Advisory revision measurements

The benchmark MUST use the existing migration CLI and Alembic graph for the selected revision range. It MUST emit JSON and human-readable results containing source commit and working-tree provenance, runner and PostgreSQL details, fixture provenance, each attempted revision's elapsed wall time, completion state and resulting revisions. Each measured step MUST retain its own observed resulting revisions after that attempt, including failed attempts, without later steps overwriting them. It MUST disclose separate subprocess and transaction semantics. Duration alone MUST NOT produce failure.

#### Scenario: Selected range completes
- **WHEN** all revisions between the selected base and target succeed
- **THEN** the report MUST identify each measured revision and its elapsed time and the resulting target stamp

#### Scenario: No pending revisions
- **WHEN** base and target are the same revision
- **THEN** the report MUST contain no measured upgrade steps and MUST identify the result as a no-op
