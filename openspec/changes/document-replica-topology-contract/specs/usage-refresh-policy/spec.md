# usage-refresh-policy Delta

## MODIFIED Requirements

### Requirement: Multi-replica leader guard

Auth Guardian SHALL use the existing leader-election mechanism so only the elected replica performs proactive refresh work. WHEN the auth guardian is enabled, the bridge instance ring has more than one member, and leader election is disabled, THEN the guardian SHALL NOT run and SHALL log a startup WARNING telling the operator to enable `CODEX_LB_LEADER_ELECTION_ENABLED`.

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
