## ADDED Requirements

### Requirement: Native WebSocket transport-event queues do not impose a fixed event-count limit

The Python native-egress adapter MUST NOT impose a fixed event-count capacity on an individual native WebSocket transport-event queue and MUST NOT terminate an otherwise healthy WebSocket solely because more than a fixed number of helper events are pending. It MUST preserve event ordering, request isolation, cancellation, helper-generation failure handling, and terminal delivery. The separate application-message queue exposed to WebSocket consumers MUST retain its existing bounded overflow behavior.

#### Scenario: Bursty native WebSocket events exceed the former limit

- **GIVEN** a native WebSocket helper emits more than 64 ordered transport events for one connection before its relay task drains them
- **WHEN** the relay consumes the connection
- **THEN** every event is delivered in order through the existing WebSocket API
- **AND** the adapter does not synthesize a `consumer_backpressure` failure from the transport-event queue

#### Scenario: Application-message backpressure remains bounded

- **GIVEN** a native WebSocket consumer stops draining application messages
- **WHEN** more than the existing message-queue capacity is received
- **THEN** the adapter preserves its existing bounded overflow failure and cleans up the native request
