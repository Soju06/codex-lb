## ADDED Requirements

### Requirement: Durable bridge claims are atomic and epoch-fenced

For an existing durable HTTP bridge session, `claim_session` MUST perform its
owner-row read, epoch decision, and write as one serialized critical section
on SQLite. Its write MUST be a compare-and-set conditioned on the session id
and the observed `(owner_instance_id, owner_epoch)`; a failed compare-and-set
MUST NOT mint or return a duplicate fencing epoch. PostgreSQL MUST retain its
existing row-locking behavior.

#### Scenario: Concurrent SQLite claims receive distinct fencing epochs

- **GIVEN** two claims concurrently target the same durable session row
- **WHEN** both claims use the production SQLite engine configuration
- **THEN** their returned owner epochs are distinct
- **AND** the durable row's account id and latest response id come from one
  complete claim rather than being split across claims
