## ADDED Requirements

### Requirement: Cache-write costs settle API-key limits

API-key cost accounting MUST include known cache-write usage at the applicable
write price with the same input partition and service-tier/context selection as
request costs. Existing reservation finalization, idempotency and cancellation
rules MUST remain unchanged.

#### Scenario: Mixed input settles once

- **GIVEN** a keyed response contains ordinary input, cached reads and cache writes
- **WHEN** its reservation is finalized
- **THEN** the cost limit receives the independently calculated total microdollars
- **AND** repeated finalization does not charge it twice
- **AND** no reservation remains pending

#### Scenario: Legacy usage omits writes

- **GIVEN** a keyed request has no cache-write usage field
- **WHEN** usage is finalized
- **THEN** its recorded cost and cost-limit settlement match the previous behavior
