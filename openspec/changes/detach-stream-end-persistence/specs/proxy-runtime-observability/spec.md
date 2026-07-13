# proxy-runtime-observability Delta

## ADDED Requirements

### Requirement: Request-log persistence is detached from the response path

Request-log rows MUST be persisted by tracked background tasks that the response/stream close does not wait for; persistence failures MUST be logged, and graceful shutdown MUST drain pending log writes up to the configured drain timeout so final requests' logs are not lost.

#### Scenario: Stream close does not wait for the log INSERT

- **GIVEN** a completed stream whose log INSERT is still pending
- **WHEN** the client observes the stream close
- **THEN** the row is not yet required to exist
- **AND** draining the persistence tasks then persists it exactly once

#### Scenario: Shutdown flushes pending log writes

- **WHEN** the service shuts down gracefully with log writes in flight
- **THEN** shutdown waits for them up to the configured drain timeout and reports tasks that failed to drain
