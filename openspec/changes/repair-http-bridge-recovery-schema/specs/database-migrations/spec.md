## ADDED Requirements

### Requirement: HTTP bridge recovery schema repair

When an installation is stamped at or beyond the deployed HTTP bridge recovery
head but lacks objects from historical additive revisions, the migration path
MUST restore `http_bridge_operations.rebind_claim_id`, transcript and replay
snapshot columns, `response_replay_input_turn_count`, the retained-alias
`target_response_id` column, and the operation recovery indexes
`idx_http_bridge_operations_session_state_created` and
`idx_http_bridge_operations_response_state`. The repair MUST be idempotent,
MUST preserve existing data, and MUST attribute restored objects to their
historical owning revisions in the ownership registry.

#### Scenario: Deployed head is missing recovery DDL

- **GIVEN** `alembic_version` is at the deployed local-login head and the
  recovery columns, indexes, or alias target are absent
- **WHEN** startup migrations run to head
- **THEN** every missing object is restored with the ORM-compatible shape
- **AND** schema-drift validation reports no differences

#### Scenario: Repair downgrade preserves parent schema

- **GIVEN** the repair restored objects on a legacy database
- **WHEN** the repair is downgraded to its immediate parent
- **THEN** repaired parent-owned columns, indexes, and alias target remain
- **AND** existing operation data is unchanged

#### Scenario: Legacy ownership markers are re-homed

- **GIVEN** an earlier repair recorded restored objects under its own revision
- **WHEN** the follow-up ownership migration runs
- **THEN** markers are moved to the historical owning revisions
- **AND** downgrading through those owners removes only objects beyond the
  requested historical target

#### Scenario: Direct historical downgrade rehomes legacy markers

- **GIVEN** a database stamped by the original repair still records restored
  objects under the repair revision
- **WHEN** an operator downgrades directly to a historical revision below one
  of those objects' owners
- **THEN** the repair downgrade MUST move each legacy marker to its historical
  owner before Alembic invokes that owner's downgrade
- **AND** the historical downgrade MUST remove only objects beyond the target
  revision
