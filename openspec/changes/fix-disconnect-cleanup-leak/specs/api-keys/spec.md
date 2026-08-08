## ADDED Requirements

### Requirement: Disconnect cleanup settles source-chat reservations

When a source-chat request is cancelled or its streaming body is closed, the proxy MUST close the upstream iterator, release its API-key reservation, and write or explicitly abort the source request-log row despite repeated cancellation delivery.

#### Scenario: Client disconnects during source stream

- **WHEN** the downstream client disconnects before source-stream completion
- **THEN** the reservation is released and the source request is logged as an aborted/error request.
