## ADDED Requirements

### Requirement: Relative upgrade target survives ledger repair

A retry of a relative upgrade interrupted by historical usage-limit ledger repair MUST use the target resolved before the ledger was rewound. An explicit upgrade target MUST remain available after an interruption.

#### Scenario: A relative upgrade stops after committing the checkpoint

- **WHEN** a relative upgrade is interrupted after committing the historical repair checkpoint and before running Alembic
- **AND** the same upgrade is retried
- **THEN** it reaches the revision resolved against the original ledger
