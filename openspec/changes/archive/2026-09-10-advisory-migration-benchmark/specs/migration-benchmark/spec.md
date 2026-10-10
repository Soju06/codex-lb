## ADDED Requirements

### Requirement: Explicit disposable benchmark invocation

The developer benchmark MUST require an explicit PostgreSQL target, disposable ownership acknowledgement, exact base and target Alembic revision IDs, fixture counts, seed and output directory. It MUST reject a nonempty database without resetting it, MUST set both database environment variables to the supplied target before app imports, and MUST NOT run during application startup or as a mandatory CI check.

#### Scenario: Existing data is protected
- **WHEN** the acknowledged target already contains user tables
- **THEN** the command MUST fail without migrating, deleting or seeding rows

### Requirement: Reproducible synthetic fixture

The benchmark MUST seed synthetic accounts and request logs at the selected base revision with deterministic values for a given seed and counts. It MUST report its fixture algorithm version, count/distribution parameters and observed aggregate counts. It MUST NOT read or copy production rows.

#### Scenario: Repeated small fixture
- **WHEN** the same parameters are used on two empty disposable databases
- **THEN** the fixture counts and distribution evidence MUST match

### Requirement: Advisory revision measurements

The benchmark MUST use the existing migration CLI and Alembic graph for the selected revision range. It MUST emit JSON and human-readable results containing source commit and working-tree provenance, runner and PostgreSQL details, fixture provenance, each attempted revision's elapsed wall time, completion state and resulting revisions. It MUST disclose separate subprocess and transaction semantics. Duration alone MUST NOT produce failure.

#### Scenario: Selected range completes
- **WHEN** all revisions between the selected base and target succeed
- **THEN** the report MUST identify each measured revision and its elapsed time and the resulting target stamp

#### Scenario: No pending revisions
- **WHEN** base and target are the same revision
- **THEN** the report MUST contain no measured upgrade steps and MUST identify the result as a no-op

### Requirement: Failure and final migration check

The benchmark MUST return nonzero for setup, fixture or upgrade failure and MUST NOT mark an incomplete attempt successful. It MUST record the failing stage and any observed resulting revisions. After a successful range it MUST run the existing public migration check and record its exit status without extending the selected range. A failed check at current head MUST fail the benchmark; a historical target MUST report the check result separately from range completion.

#### Scenario: Upgrade fails
- **WHEN** a selected upgrade exits unsuccessfully
- **THEN** the report MUST retain failed status, measured attempts and the resulting revision evidence

#### Scenario: Current head is checked
- **WHEN** the selected target is the current source's Alembic head
- **THEN** success MUST require the public migration check to succeed
