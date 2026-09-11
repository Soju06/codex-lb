## ADDED Requirements

### Requirement: Affinity history converges with dashboard authentication history
The system MUST provide a single migration head when the published affinity history is combined with dashboard role and user authentication migrations. Convergence MUST preserve all published revision definitions and apply each missing branch once.

#### Scenario: Upgrade either populated history
- **GIVEN** a database at the published affinity merge or the dashboard authentication leaf
- **WHEN** it upgrades to head
- **THEN** existing account ownership, request logs, guest generations, roles, grants, users, identities and audit history MUST remain intact except for the existing authentication migrations' specified backfills
- **AND** historical logs newly receiving affinity columns MUST retain null affinity metadata

#### Scenario: Merge-only downgrade and reupgrade
- **GIVEN** a database has reached the converged head and a user's credentials or session generation have subsequently changed
- **WHEN** only the merge revision is downgraded and then upgraded again
- **THEN** both parent histories MUST remain applied and all schema and data MUST be preserved
- **AND** the earlier credential projection MUST NOT run again
