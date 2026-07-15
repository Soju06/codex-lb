## ADDED Requirements

### Requirement: Compact preflight budget exhaustion settles forwarded API-key reservations

When a compact request holds a forwarded API-key usage reservation and a budget-exhaustion check will terminate `compact_responses` without reaching an existing settlement handler, the system MUST settle the reservation with no response usage before raising the budget-exhausted error. This requirement MUST cover exhaustion before freshness evaluation, when no freshness-check reserve remains, after freshness evaluation, and before a post-401 forced-refresh attempt. Existing account-lease release behavior and the external `502 upstream_request_timeout` response MUST remain unchanged.

Budget-exhaustion errors raised inside `_call_compact` MUST continue to be settled only by their enclosing retry-loop error handler, so a reservation is settled exactly once.

#### Scenario: Forwarded compact preflight exhausts its budget

- **GIVEN** a forwarded compact request holds an unsettled API-key usage reservation
- **WHEN** a compact preflight budget check terminates the request before an upstream compact call
- **THEN** the system releases the held reservation before raising the error
- **AND** the client receives the existing `502 upstream_request_timeout` response

#### Scenario: Post-401 forced-refresh preflight exhausts its budget

- **GIVEN** a forwarded compact request holds an unsettled API-key usage reservation
- **AND** its upstream compact attempt returns `401`
- **WHEN** the remaining request budget is exhausted before forced refresh
- **THEN** the system releases the held reservation before raising the budget-exhausted error
- **AND** the client receives the existing `502 upstream_request_timeout` response

#### Scenario: Inner compact-call budget exhaustion is settled exactly once

- **GIVEN** a compact request reaches an inner `_call_compact` budget check
- **WHEN** that check raises `upstream_request_timeout`
- **THEN** the enclosing retry-loop error handler settles the reservation exactly once
- **AND** the inner budget terminal does not perform an additional settlement
