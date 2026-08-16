# responses-api-compat Delta

## ADDED Requirements

### Requirement: HTTP bridge retry circuits count each upstream send attempt at most once

For an HTTP Responses bridge request, multiple local failure observers that
classify the same upstream `response.create` send attempt MUST contribute at
most one consecutive retry-circuit failure and at most one durable failure
persistence operation. A separately dispatched retry or replay MUST be treated
as a new send attempt and MAY contribute the next eligible failure under the
existing retry-circuit policy.

The proxy MUST capture the attempt being classified before awaiting recovery,
reconnection, settlement, or pending-request ownership that can dispatch a
newer attempt. When stale ownership is classified while holding the pending
lock, the classified request set and its attempt selection MUST come from that
same snapshot. A send attempt that is disarmed by send-failure or cancellation
cleanup, or that observes a matching upstream response lifecycle event before
its first failure claim, MUST NOT add a retry-circuit failure. A matched
response lifecycle event MUST mark its attempt observed even when downstream
delivery or ordinary response-event accounting is intentionally deferred.

The proxy MUST distinguish a failure path with no attempt identity from one
whose attempt identity is present but ineligible or ambiguous. Only the former
MAY preserve legacy unscoped recording. An ineligible attempt or multiple
eligible candidates MUST NOT fall back to an unscoped failure. Duplicate
observers MUST wait for the first claim's settlement and then use the current
circuit count; they MUST NOT expose a cached historical count after a later
failure or successful clear. Deduplication MUST NOT change existing failure
classes, thresholds, cooldowns, continuity guards, or cross-replica conflict
merging.

#### Scenario: reader and downstream watchdogs observe one eventless send

- **GIVEN** one hard-affinity HTTP bridge `response.create` send remains eventless
- **AND** the upstream reader watchdog and downstream stream-idle watchdog both classify that send
- **WHEN** both observers report the retry-circuit failure
- **THEN** the circuit's consecutive failure count increases by exactly one
- **AND** the failure is durably persisted exactly once
- **AND** the default two-failure circuit does not open from that send alone

#### Scenario: a separately dispatched retry is a second failure

- **GIVEN** one send attempt has already contributed one retry-circuit failure
- **WHEN** a later retry or replay dispatches a new `response.create` and that attempt also fails eligibility checks
- **THEN** the new attempt contributes a second failure
- **AND** the existing threshold and cooldown behavior may open the circuit

#### Scenario: a delayed old observer cannot count a newer attempt

- **GIVEN** an observer captured attempt A before recovery dispatched attempt B
- **AND** attempt A has already contributed its failure
- **WHEN** the delayed observer resumes after attempt B is current
- **THEN** it does not increment or persist another failure for attempt A
- **AND** it does not mark attempt B as recorded
- **AND** it observes the current circuit count, including attempt B's independent failure

#### Scenario: an upstream response wins the timeout race

- **GIVEN** a watchdog is evaluating an eventless send attempt
- **WHEN** a matching upstream response lifecycle event is observed before the attempt's first failure claim
- **THEN** that attempt does not contribute a retry-circuit failure

#### Scenario: a deferred reasoning prelude wins the timeout race

- **GIVEN** a matched reasoning lifecycle event is held for deferred downstream delivery
- **AND** ordinary response-event accounting remains zero for that prelude
- **WHEN** an eventless failure observer evaluates the same send attempt
- **THEN** the attempt is already marked as response-observed
- **AND** it does not contribute or persist a retry-circuit failure
- **AND** deferred-delivery and downstream-visibility behavior remain unchanged

#### Scenario: multiple pending attempts are ambiguous at a shared failure boundary

- **GIVEN** a shared cleanup boundary contains multiple distinct eligible send attempts
- **WHEN** the boundary cannot attribute its failure to exactly one physical send
- **THEN** it does not fall back to an unscoped retry-circuit failure
- **AND** it does not mark any candidate attempt as recorded
- **AND** a later observer with an exact attempt identity can still record each genuine failure independently

#### Scenario: pending-lock wait cannot replace the classified attempt

- **GIVEN** stale cleanup captures attempt A for a request before acquiring pending ownership
- **AND** recovery installs attempt B while cleanup is waiting for the pending lock
- **WHEN** cleanup later records the classified failure
- **THEN** it retains attempt A's identity
- **AND** it does not mark attempt B as recorded

#### Scenario: a cleared circuit is not recreated by a delayed duplicate

- **GIVEN** a send attempt contributed a failure and a later successful terminal response cleared the circuit
- **WHEN** another observer of the old send attempt resumes
- **THEN** the old observer does not recreate or persist the cleared failure
- **AND** it receives the current circuit count of zero
