# usage-refresh-policy Delta

## MODIFIED Requirements

### Requirement: Multi-replica leader guard

Auth Guardian SHALL use the existing leader-election mechanism so only the elected replica performs proactive refresh work. WHEN the auth guardian is enabled, the statically configured bridge instance ring has more than one member, and leader election is disabled, THEN the guardian SHALL NOT run and SHALL log a startup WARNING telling the operator to enable `CODEX_LB_LEADER_ELECTION_ENABLED`.

Because Helm and compose deployments deliberately leave `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING` empty and instead register replicas dynamically in the DB-backed `bridge_ring_members` ring, the build-time guard alone cannot observe those replicas. Therefore, WHEN leader election is disabled, each refresh pass SHALL first count the live `bridge_ring_members` heartbeats and, IF more than one replica is live, SHALL skip that pass and log a WARNING telling the operator to enable `CODEX_LB_LEADER_ELECTION_ENABLED`. WHEN leader election is enabled, the elected-leader gate alone determines who runs and the dynamic ring count SHALL NOT be consulted.

#### Scenario: Replica is not leader

- **GIVEN** leader election is enabled
- **AND** the current replica does not acquire leadership
- **WHEN** Auth Guardian wakes
- **THEN** the scheduler skips refresh work for that pass

#### Scenario: Multi-replica ring without leader election disables the guardian loudly

- **GIVEN** the auth guardian is enabled
- **AND** the bridge instance ring has more than one member
- **AND** leader election is disabled
- **WHEN** the guardian scheduler is built at startup
- **THEN** the guardian is disabled
- **AND** a WARNING is logged telling the operator to set `CODEX_LB_LEADER_ELECTION_ENABLED=true`

#### Scenario: Dynamic bridge ring without leader election skips the refresh pass

- **GIVEN** the auth guardian is enabled
- **AND** the statically configured bridge instance ring is empty
- **AND** more than one replica is live in the DB-backed `bridge_ring_members` ring
- **AND** leader election is disabled
- **WHEN** a refresh pass runs
- **THEN** the pass performs no proactive refresh work
- **AND** a WARNING is logged telling the operator to set `CODEX_LB_LEADER_ELECTION_ENABLED=true`

#### Scenario: Dynamic ring count is not consulted when leader election is enabled

- **GIVEN** the auth guardian is enabled
- **AND** leader election is enabled
- **AND** the current replica acquires leadership
- **WHEN** a refresh pass runs
- **THEN** the scheduler performs refresh work without counting live `bridge_ring_members`
