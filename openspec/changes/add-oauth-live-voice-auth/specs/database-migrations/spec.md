## ADDED Requirements

### Requirement: OAuth Live policy schema is optional and relational

The database SHALL store at most one OAuth Live policy per caller Account and an explicit set of allowed upstream Accounts. Policy and relationship rows MUST contain no OAuth token, email, session id, call id, SDP, or frame content. Account deletion MUST cascade removal of dependent policy and relationship rows. The Alembic revision MUST be linear on the current head and reversible on SQLite and PostgreSQL.

#### Scenario: Existing installs remain disabled after upgrade

- **WHEN** an existing database upgrades to the OAuth Live policy revision
- **THEN** both policy tables exist and contain no automatically enabled policy
- **AND** ordinary proxy and dashboard startup require no new setup action

#### Scenario: Downgrade removes policy schema in dependency order

- **WHEN** the revision downgrades
- **THEN** the allowed-account relationship table is removed before the policy table
- **AND** the prior Alembic head is restored without multiple heads

