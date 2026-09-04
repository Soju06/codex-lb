## ADDED Requirements

### Requirement: Blue/green overlap satisfies multi-replica invariants

The HAProxy blue/green Compose workflow MUST refuse to start an overlapping candidate unless both slots use a shared PostgreSQL database, identical shared encryption-key material, unique stable bridge instance identifiers, and distinct replica-specific bridge advertise URLs reachable between slots. Leader election MUST remain enabled during overlap. Both slots MUST use the project-owned graceful-drain launcher and MUST NOT publish their application ports on the host.

#### Scenario: SQLite configuration is rejected

- **WHEN** the HA deployment command resolves an absent, SQLite, or otherwise non-PostgreSQL database URL
- **THEN** it exits before starting a second application slot
- **AND** the active deployment remains unchanged

#### Scenario: Replica identity is deterministic and unique

- **WHEN** blue and green overlap
- **THEN** each slot has its own stable bridge instance ID and matching replica-specific advertise URL
- **AND** both share the same encryption-key volume and database

#### Scenario: Leader election is explicitly disabled

- **WHEN** the HA deployment environment disables leader election
- **THEN** deployment exits before starting a second slot
- **AND** reports that leader election is required for overlapping replicas
