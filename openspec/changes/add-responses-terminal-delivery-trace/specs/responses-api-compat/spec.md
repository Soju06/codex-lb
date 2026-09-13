## MODIFIED Requirements

### Requirement: Terminal stream settlement is immutable after delivery

When a Responses stream has observed a terminal event (`response.completed`, `response.failed`, `response.incomplete`, or `error`) from upstream and handed it to the downstream writer, a later downstream cancellation MUST NOT rewrite the terminal status, error, usage, or account-health settlement. Whether the frame reached the client is reported separately by the terminal-delivery trace and never rewrites settlement.

#### Scenario: Disconnect after terminal event

- **WHEN** the downstream closes after receiving a terminal event
- **THEN** the request log and settlement retain the terminal event's outcome
- **AND** the proxy does not record `client_disconnected` for that stream.
