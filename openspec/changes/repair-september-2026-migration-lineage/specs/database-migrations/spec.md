## ADDED Requirements

### Requirement: SCIM and overflow retirement form one ordered lineage

The migration graph MUST keep `20260914_000000_add_scim_tokens` unchanged and
MUST place `20260918_171648_drop_subscription_overflow_schema` directly after
it. The two revisions MUST use distinct timestamp slots and the graph MUST have
one head.

#### Scenario: Startup migration targets one head

- **GIVEN** both the SCIM and overflow-retirement revisions are present
- **WHEN** Alembic resolves `head`
- **THEN** it MUST resolve the overflow-retirement revision as the single head
- **AND** startup MUST NOT fail with `MultipleHeads`

#### Scenario: The later revision follows SCIM

- **GIVEN** a database stamped at `20260914_000000_add_scim_tokens`
- **WHEN** the normal migration runner upgrades to `head`
- **THEN** it MUST apply the overflow-retirement revision according to its contract
- **AND** it MUST finish stamped at the single graph head

#### Scenario: The combined graph round-trips

- **GIVEN** a disposable database upgraded to the single head
- **WHEN** it downgrades to the branches' shared parent and upgrades to `head` again
- **THEN** the SCIM schema MUST be present
- **AND** the withdrawn overflow schema MUST be absent
