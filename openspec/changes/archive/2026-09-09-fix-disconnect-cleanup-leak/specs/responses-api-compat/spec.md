## ADDED Requirements

### Requirement: Terminal stream settlement is immutable after delivery

When a Responses stream has observed and delivered a terminal event (`response.completed`, `response.failed`, `response.incomplete`, or `error`), a later downstream cancellation MUST NOT rewrite the terminal status, error, usage, or account-health settlement.

#### Scenario: Disconnect after terminal event

- **WHEN** the downstream closes after receiving a terminal event
- **THEN** the request log and settlement retain the terminal event's outcome
- **AND** the proxy does not record `client_disconnected` for that stream.
