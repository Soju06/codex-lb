## ADDED Requirements

### Requirement: Security lineage persistence survives account ownership changes

The migration lineage SHALL persist the monotonic
`requires_security_work_authorized` property independently of the account that
currently owns a response lineage. Alembic and ORM metadata SHALL represent the
same nullable ownership and security-marker columns, and migration reconciliation
SHALL be idempotent for both fresh databases and databases that already ran the
broader security-work live stack.

Detached security markers SHALL NOT be returned as ordinary account usage or
deleted by age-based sticky-session cleanup. A downgrade SHALL NOT remove
existing compatibility columns or detached markers when their original owning
revision cannot be proven.

#### Scenario: Fresh migration creates the focused security-lineage schema

- **WHEN** a fresh database upgrades to Alembic `head`
- **THEN** usage, sticky-session, and durable bridge tables contain the focused
  security-lineage columns represented by ORM metadata
- **AND** migration policy reports one head with no schema drift

#### Scenario: Existing live-stack schema reconciles idempotently

- **GIVEN** a database already contains any subset of the security-lineage,
  pending tool-call, or retained quota-planner compatibility columns
- **WHEN** the focused reconciliation revision upgrades that database
- **THEN** it adds only missing columns and markers
- **AND** a repeated upgrade leaves the same schema and data intact

#### Scenario: Security requirement survives owner deletion

- **GIVEN** a sticky or durable response lineage requires a security-work
  authorized account
- **WHEN** its current account is deleted or ownership is cleared
- **THEN** a detached, account-less marker retains the monotonic requirement
- **AND** later selection cannot treat that lineage as authorization-neutral

#### Scenario: Detached markers stay out of ordinary usage windows

- **GIVEN** a retained security-lineage usage row has no account owner
- **WHEN** account usage windows are queried or mapped
- **THEN** the detached row is not exposed as an ordinary account usage window
- **AND** the evidence row remains available to migration-compatible storage

