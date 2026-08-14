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
reconnection, or settlement that can dispatch a newer attempt. A send attempt
that is disarmed by send-failure or cancellation cleanup, or that observes a
matching upstream response lifecycle event before its first failure claim,
MUST NOT add a retry-circuit failure. Deduplication MUST NOT change existing
failure classes, thresholds, cooldowns, continuity guards, or cross-replica
conflict merging.

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

#### Scenario: an upstream response wins the timeout race

- **GIVEN** a watchdog is evaluating an eventless send attempt
- **WHEN** a matching upstream response lifecycle event is observed before the attempt's first failure claim
- **THEN** that attempt does not contribute a retry-circuit failure

#### Scenario: a cleared circuit is not recreated by a delayed duplicate

- **GIVEN** a send attempt contributed a failure and a later successful terminal response cleared the circuit
- **WHEN** another observer of the old send attempt resumes
- **THEN** the old observer does not recreate or persist the cleared failure
