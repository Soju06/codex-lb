## MODIFIED Requirements

### Requirement: Leader election defaults and configuration

`leader_election_enabled` SHALL default to true. `leader_election_ttl_seconds` SHALL default to 60 and MUST reject values below 5. Disabling leader election MUST cause the leader gate to treat every replica as leader (single-instance escape hatch), and this consequence is documented in the capability context.

The Auth Guardian scheduler is the one exception to the escape hatch: because it force-refreshes OAuth tokens and concurrent force refreshes across replicas can invalidate rotated refresh tokens, in a multi-replica deployment (instance ring larger than one) with leader election disabled the Auth Guardian scheduler MUST perform no refresh work, and its builder MUST emit an operator-visible warning log stating that the guardian is disabled for this reason. The scheduler loop still starts — its enablement is a dashboard setting read at the start of every pass, so a topology-blocked replica keeps reading that setting instead of deciding once at startup — and every pass SHALL skip while the topology blocks it, whatever the dashboard setting says.

#### Scenario: Fresh two-replica deployment with default configuration

- **GIVEN** two replicas share one PostgreSQL database with default environment
- **WHEN** singleton schedulers tick
- **THEN** only one replica runs singleton scheduler work

#### Scenario: TTL below the minimum

- **GIVEN** `CODEX_LB_LEADER_ELECTION_TTL_SECONDS=2`
- **WHEN** settings are loaded
- **THEN** validation fails

#### Scenario: Multi-replica ring with leader election disabled

- **GIVEN** an instance ring with two replicas and `CODEX_LB_LEADER_ELECTION_ENABLED=false`
- **AND** the effective `auth_guardian_enabled` setting is `true`
- **WHEN** the Auth Guardian scheduler is built and a refresh pass runs
- **THEN** the pass performs no refresh work
- **AND** a warning log states that the guardian is disabled because the ring runs without leader election
