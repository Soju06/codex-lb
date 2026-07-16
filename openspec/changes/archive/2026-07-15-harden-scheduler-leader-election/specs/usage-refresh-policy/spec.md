# usage-refresh-policy

## MODIFIED Requirements

### Requirement: Multi-replica leader guard

Auth Guardian SHALL gate proactive refresh work on the scheduler leader lease defined by the `scheduler-coordination` capability so only the elected replica performs proactive refresh work.

#### Scenario: Replica is not leader

- **GIVEN** leader election is enabled
- **AND** the current replica does not acquire leadership
- **WHEN** Auth Guardian wakes
- **THEN** the scheduler skips refresh work for that pass

#### Scenario: Lease is lost during a guardian pass

- **GIVEN** a guardian refresh pass is in flight on the elected leader
- **WHEN** the leader lease is lost
- **THEN** the in-flight pass is cancelled rather than continuing to force-refresh tokens
