## ADDED Requirements

### Requirement: Recovery and dashboard settings histories converge

The migration graph MUST have one head joining the existing recovery merge and
dashboard timeout, routing/overload settings, report-rollup, automation claim-budget,
dashboard Codex prewarm, and dashboard stream/bridge budget revisions.
The joining revisions MUST NOT alter schema
or data, and historical parent links MUST remain unchanged.

#### Scenario: Upgrade from either branch

- **WHEN** a database upgrades from either existing branch to the new head
- **THEN** it contains both recovery and dashboard settings schema
- **AND** existing account, operation, and settings data is preserved

#### Scenario: Undo the metadata-only join

- **WHEN** the joining revision is downgraded to a named parent
- **THEN** its metadata-only downgrade preserves schema and data
