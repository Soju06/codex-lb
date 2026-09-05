## MODIFIED Requirements

### Requirement: Fenced-out replicas evict their local bridge session
When a replica discovers through a fenced renewal or fenced alias write that another instance or epoch owns the durable session, it MUST close its local in-memory bridge session — closing the upstream websocket and releasing the account lease — instead of adopting the new epoch and continuing to serve. A replica MUST also reconcile durable ownership for local sessions whose lease is past its TTL on an independently supervised periodic cadence and close any session that has been fenced out, so orphaned upstream connections and account leases are bounded by the lease TTL rather than the idle TTL. Reconciliation MUST NOT execute in the ring-heartbeat renewal critical path.

#### Scenario: Fenced-out renewal closes the local session
- **GIVEN** replica A holds a local bridge session and replica B took over the durable row
- **WHEN** replica A's lease renewal is fenced out
- **THEN** replica A detaches and closes the local session, releasing its account lease and upstream websocket
- **AND** the request fails with the retryable bridge-instance-mismatch error instead of riding the fenced-out session

#### Scenario: Heartbeat reconciliation closes fenced-out idle sessions
- **GIVEN** replica A holds an idle local bridge session whose durable lease expired
- **AND** replica B has since claimed the durable row
- **WHEN** replica A's periodic ownership reconciliation runs
- **THEN** replica A closes the fenced-out local session
- **AND** local sessions still owned by replica A are left untouched

#### Scenario: Reconciliation lookups survive large candidate sets
- **GIVEN** more local sessions are past the lease TTL than fit in one database `IN (...)` parameter list
- **WHEN** the reconciliation sweep batch-loads the durable rows
- **THEN** the lookup is chunked so every candidate resolves and fenced-out sessions are still evicted

## ADDED Requirements

### Requirement: Periodic bridge upkeep is isolated and bounded
Every replica MUST run durable-ownership reconciliation and idle bridge-session sweeping independently of request traffic, ring-heartbeat renewal, and each other. Each phase MUST have a bounded wait, MUST retain one strongly referenced owner until completion or shutdown abandonment, and MUST NOT start a duplicate invocation while its previous owner remains unfinished. A phase failure or timeout MUST NOT terminate future cycles of another phase or ring renewal. Existing reconciliation, idle eligibility, and bounded close semantics MUST remain unchanged.

#### Scenario: Quiet replica still sweeps idle sessions
- **GIVEN** a replica holds a bridge session past its idle TTL and receives no further bridge requests
- **WHEN** its independent idle-sweep phase runs
- **THEN** the existing idle eligibility selects the session
- **AND** the existing bounded close path owns its closure

#### Scenario: Blocked reconciliation does not skip idle sweeping
- **GIVEN** durable-ownership reconciliation remains unfinished past its phase deadline
- **WHEN** the idle-sweep cadence becomes due
- **THEN** the idle sweep runs under its own owner
- **AND** no second reconciliation invocation starts while the first remains unfinished

#### Scenario: Failed idle sweep does not stop later reconciliation
- **WHEN** one idle-sweep invocation fails
- **THEN** the failure is recorded
- **AND** later durable-ownership reconciliation cycles remain scheduled

#### Scenario: Shutdown drains periodic bridge upkeep
- **WHEN** graceful shutdown begins with a bridge maintenance phase in flight
- **THEN** the phase owner is cancelled and drained within the existing shutdown deadline
- **AND** no maintenance task is left untracked
