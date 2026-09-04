## ADDED Requirements

### Requirement: Request-log endpoints accept server-authoritative timeframes

`GET /api/request-logs` and `/api/request-logs/options` MUST accept
`timeframe=1h|24h|7d` and derive each effective lower bound from the server UTC
clock. Symbolic requests MUST NOT require a browser timestamp. `timeframe` and
`since` MUST NOT be supplied together. Existing standalone `since` and `until`
MUST retain behavior; `until` MAY accompany a timeframe.

#### Scenario: Symbolic timeframe advances on refresh

- **WHEN** the server clock advances and a client refetches `timeframe=1h`
- **THEN** listing and options derive a fresh lower bound
- **AND** all other filters remain intact

#### Scenario: Ambiguous lower bounds are rejected

- **WHEN** a caller supplies both `timeframe` and `since`
- **THEN** the endpoint returns HTTP 422

### Requirement: Request-log total cache uses semantic window identity

In symbolic mode, total-cache identity MUST use `("timeframe", timeframe)`.
Legacy mode MUST use `("since", effective_since)`. Membership rows MUST always
use the live derived timestamp.

#### Scenario: Repeated timeframe requests reuse count metadata

- **GIVEN** two requests use the same filters and timeframe within cache TTL
- **WHEN** their derived timestamps differ
- **THEN** the count query executes once
- **AND** each membership query uses its live timestamp
