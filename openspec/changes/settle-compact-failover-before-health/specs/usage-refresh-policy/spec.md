## ADDED Requirements

### Requirement: Compact failover settles before account-health writes

When `compact_responses` holds an API-key usage reservation, it MUST NOT write account health for a compact upstream failure until that reservation has been settled or released. A `failover_next` decision MUST keep the same reservation for the next account and MUST defer the failed account's health write until the next settlement. Timeout and exhaustion terminals MUST keep settle-then-health order. Compact MUST NOT acquire a second reservation mid-request.

#### Scenario: Compact failover_next defers health until settle

- **GIVEN** a compact request with a held API-key reservation
- **AND** the first account fails with a `failover_next` class
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_stream_error` for the failed account runs only after that settlement
- **AND** the request does not acquire another reservation

#### Scenario: Compact timeout still settles before health

- **GIVEN** a compact request whose upstream call times out
- **WHEN** the timeout branch records account health
- **THEN** the reservation is settled before `_handle_stream_error`
