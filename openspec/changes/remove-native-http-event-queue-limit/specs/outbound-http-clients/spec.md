## ADDED Requirements

### Requirement: Native HTTP response queues have no fixed event-count limit

The Python native-egress adapter MUST NOT impose a fixed event-count capacity on an individual HTTP response queue and MUST NOT terminate an HTTP response solely because more than a fixed number of native helper events are pending. It MUST preserve event ordering, request isolation, cancellation, and terminal delivery. Native WebSocket message queues MUST retain their existing bounded behavior.

#### Scenario: Bursty HTTP response exceeds the former event count

- **GIVEN** a native HTTP helper emits more than 64 ordered chunks for one response before its Python consumer drains them
- **WHEN** the consumer reads the response body
- **THEN** every chunk is delivered in order through the terminal event
- **AND** the adapter does not raise `consumer_backpressure`

#### Scenario: WebSocket buffering remains bounded

- **WHEN** a native WebSocket consumer stops draining its message queue
- **THEN** the existing bounded overflow behavior remains in effect
