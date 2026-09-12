## ADDED Requirements

### Requirement: Recovery and dashboard settings histories converge

The migration graph MUST have one head joining the existing recovery merge and
dashboard timeout, routing/overload settings, report-rollup, automation claim-budget,
dashboard Codex prewarm, and dashboard stream/bridge budget revisions.
The joining revisions MUST NOT alter schema
or data, and historical parent links MUST remain unchanged.

In particular, the deployed
`20260911_020000_add_http_bridge_terminal_append_phase` revision MUST retain its
original parent `20260911_010000_merge_pin_index_and_affinity_heads`. Any later
`20260911_015000_merge_http_bridge_and_affinity_heads` lineage MUST be joined by
a separate metadata-only merge revision rather than by rewriting that parent.

#### Scenario: Upgrade from either branch

- **WHEN** a database upgrades from either existing branch to the new head
- **THEN** it contains both recovery and dashboard settings schema
- **AND** existing account, operation, and settings data is preserved

#### Scenario: Deployed migration parent remains stable

- **WHEN** the migration graph is rebased onto a newer upstream main
- **THEN** the terminal-append revision keeps its original parent
- **AND** the separate affinity lineage is converged by a new metadata-only
  merge without changing either historical revision.

#### Scenario: Undo the metadata-only join

- **WHEN** the joining revision is downgraded to a named parent
- **THEN** its metadata-only downgrade preserves schema and data
