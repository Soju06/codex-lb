## ADDED Requirements

### Requirement: Guarded recovery guidance for unknown revisions

When an upgrade rejects revisions unknown to the running build, its diagnostic SHALL retain the matching-or-newer-image option and describe `python -m app.db.migrate stamp <revision>` as a conditional metadata-only operation. It MUST state that stamping does not roll back schema or data, requires verified schema and data compatibility with the rollback image, and must run from a build containing both the recorded and target revisions after migration transactions have ended and a recoverable database backup and encryption key have been preserved. The failed upgrade MUST NOT automatically stamp or alter application data. Operator documentation SHALL explain selecting an explicit target revision, preserving the database target and encryption key, verifying compatibility on a disposable copy, and checking the rollback image before resuming traffic.

#### Scenario: Upgrade against a newer revision

- **GIVEN** a database records a revision unknown to the running build
- **WHEN** the operator runs the migration upgrade CLI
- **THEN** the command fails with the unknown-revision diagnostic and guarded stamp guidance
- **AND** the recorded revision, schema and application data remain unchanged

#### Scenario: Unknown revision cannot be stamped by the old build

- **GIVEN** the current revision is absent from the running build
- **WHEN** the operator runs the existing stamp CLI to a locally known revision
- **THEN** the command fails without changing the revision
- **AND** documentation directs the operator to a build containing both revisions instead of deleting or purging migration metadata
